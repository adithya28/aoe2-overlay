import ast
import asyncio
import base64
import binascii
import copy
import json
import logging
import os
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiohttp
import websockets
from dotenv import load_dotenv

from constants import MATCH_TYPE_LEADERBOARD_MAP, CIV_MAPPINGS, POLLING_INTERVAL
from flags import is_logged_in, is_in_game

load_dotenv()


class WebSocketBroadcaster:
    def __init__(self, host="localhost", port=8765):
        self.host = host
        self.port = port
        self.clients = set()

    async def handler(self, websocket):
        self.clients.add(websocket)
        try:
            async for _ in websocket:
                pass  # we don't expect messages from the client
        finally:
            self.clients.remove(websocket)

    async def start(self):
        async with websockets.serve(self.handler, self.host, self.port):
            await asyncio.Future()  # run forever

    async def broadcast(self, message: str):
        if self.clients:
            await asyncio.gather(
                *[client.send(message) for client in self.clients],
                return_exceptions=True
            )


class PlayerStatsFetcher:
    """Polls match and player stats, writes enriched map/player data to JSON."""

    def __init__(
            self,
            proxy_url: str = "http://127.0.0.1:5000",
            output_file: str = "player_stats.json",
            poll_interval: int = POLLING_INTERVAL
    ):
        self.proxy_url = proxy_url.rstrip('/')
        self.watch_profiles = os.getenv("TARGET_STEAM_PROFILE_ID")
        self.output_file = Path(output_file)
        self.poll_interval = poll_interval
        self.logger = self._setup_logging()
        self.session: Optional[aiohttp.ClientSession] = None

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("PlayerStatsFetcher")
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        return logger

    async def run(self):
        broadcaster = WebSocketBroadcaster()
        self.logger.info(f"Starting Websocket server at port 8765")
        ws_task = asyncio.create_task(broadcaster.start())

        async with aiohttp.ClientSession() as self.session:
            try:
                self.logger.info(f"Starting fetcher. Watching profiles: {self.watch_profiles}")

                while True:
                    try:
                        # 1. Fetch raw matches data
                        await is_logged_in.wait()

                        matches_data = await self._fetch_matches()
                        if matches_data is None:
                            await asyncio.sleep(self.poll_interval)
                            continue

                        # 2. Parse match details (map, players etc.)
                        match_summary = self._parse_match_summaries(matches_data)
                        if not match_summary:
                            self.logger.info("No matches found. Clearing in_game flag")
                            is_in_game.clear()
                            await asyncio.sleep(self.poll_interval)
                            continue

                        # 3. Collect unique profile IDs from these matches
                        profile_ids = set()
                        for p in match_summary["players_civ_mappings"]:
                            profile_ids.add(p["profile_id"])

                        self.logger.info(f"Found matches with {len(profile_ids)} unique players.")

                        # 4. Determine relevant leaderboard IDs from the matches' game types
                        relevant_leaderboard_id = self._get_relevant_leaderboard_id(match_summary)
                        self.logger.debug(f"Relevant leaderboard IDs: {relevant_leaderboard_id}")

                        # 5. Fetch personal stats for all seen players
                        stats_data = await self._fetch_personal_stats(list(profile_ids), relevant_leaderboard_id)
                        if not stats_data:
                            self.logger.warning("No stats returned.")
                        player_data = self._build_player_data(matches_data, stats_data)
                        # 7. Write everything to JSON
                        clean_output = self.clean_player_match_data(match_summary, player_data)
                        json.dump(clean_output, open(self.output_file, "w"))
                        is_in_game.set()
                        # --- Broadcast via WebSocket ---
                        if broadcaster:
                            asyncio.create_task(broadcaster.broadcast(json.dumps(clean_output)))
                        self.logger.info(f"Saved matches and {len(player_data)} players.")



                    except asyncio.CancelledError:
                        self.logger.info("Fetcher stopped.")
                        break
                    except Exception as e:
                        self.logger.error(f"Unexpected error: {e}", exc_info=True)

                    await asyncio.sleep(self.poll_interval)

            finally:
                ws_task.cancel()
                try:
                    await ws_task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------
    # API Calls
    # ------------------------------------------------------------------
    async def _fetch_matches(self) -> Optional[List]:
        try:
            params = {
                "profile_ids": str([self.watch_profiles, self.watch_profiles]),
            }
            url = f"{self.proxy_url}/relic/game/advertisement/findObservableAdvertisements"
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    raw_text = await resp.text()
                    self.logger.debug(f"Raw match response (first 200): {raw_text[:200]}")
                    return json.loads(raw_text)
                else:
                    text = await resp.text()
                    self.logger.error(f"Match API error {resp.status}: {text[:200]}")
                    return None
        except Exception as e:
            self.logger.error(f"Exception fetching matches: {e}")
            return None

    async def _fetch_personal_stats(self, profile_ids: List[int], leaderboard_id) -> Dict[int, Dict]:
        if not profile_ids:
            return {}
        all_stats = {}
        batch_size = 50
        for i in range(0, len(profile_ids), batch_size):
            batch = profile_ids[i:i + batch_size]
            try:
                ids_str = f"[{','.join(map(str, batch))}]"
                params = {"profile_ids": ids_str}
                url = f"{self.proxy_url}/relic/community/leaderboard/GetPersonalStat"
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        raw_text = await resp.text()
                        data = json.loads(raw_text)
                        for statgroup in data.get("statGroups", []):
                            current_statgroup_id = statgroup.get("id")
                            member_data = statgroup.get("members", [])[0]
                            for leaderboard in data.get("leaderboardStats", []):
                                if leaderboard.get("statgroup_id") == current_statgroup_id and leaderboard.get(
                                        "leaderboard_id") == leaderboard_id:
                                    all_stats[member_data.get('profile_id')] = {
                                        'leaderboard_id': leaderboard_id,
                                        'alias': member_data.get("alias"),
                                        'country': member_data.get("country"),
                                        'elo': leaderboard.get("rating"),
                                        'rank': leaderboard.get("rank"),
                                        'streak': leaderboard.get("streak")
                                    }
                    else:
                        text = await resp.text()
                        self.logger.error(f"Stats API error {resp.status}: {text[:200]}")
            except Exception as e:
                self.logger.error(f"Stats batch exception: {e}")
        return all_stats

    def _parse_match_summaries(self, matches_data: List) -> Any:
        """Extract match-level info and player slots from the raw response."""
        try:
            if not matches_data or len(matches_data) < 2:
                return None
            matches_list = matches_data[1]
            for match in matches_list:
                if len(match) < 15:  # Need at least up to players_in_match (index 14)
                    continue
                # Basic match info
                summary = {
                    "match_id": match[0],
                    "lobby_name": match[5] if len(match) > 5 else "",
                    "game_mode": match[11] if len(match) > 11 else "",
                    "map_name": self.parse_settings(match[9]) if len(match) > 9 else "",
                    "slot_info": self.decode_b64_slotinfo(match[12]) if len(match) > 12 else None,
                    "game_type": match[13] if len(match) > 13 else 0,
                }
                # Player slots (index 14)
                players = []
                if len(match) > 14 and isinstance(match[14], list):
                    for slot in match[14]:
                        if len(slot) < 7:
                            continue
                        players.append({
                            "profile_id": slot[1],
                            "team": slot[5],
                            "civilization_id": slot[4],
                        })
                summary["players_civ_mappings"] = players
                return summary
        except Exception as e:
            self.logger.error(f"Error parsing match summaries: {e}")
            return None

    def _get_relevant_leaderboard_id(self, match_summary: Dict) -> int:
        """Collect leaderboard IDs for all game types present in the matches."""
        gt = match_summary.get("game_type")
        if gt is not None and gt in MATCH_TYPE_LEADERBOARD_MAP:
            return MATCH_TYPE_LEADERBOARD_MAP[gt]
        return 0

    def decode_b64_slotinfo(self, encoded_str: str) -> List:
        """
        Decode a string that was created as base64(zlib(base64(x))).
        Returns the original bytes x.

        Example:
            decoded_bytes = decode_b64_zlib_b64(match_data["options"])
            # decoded_bytes is the raw binary settings (parse with struct, etc.)
        """
        try:
            # Step 1: base64 decode -> zlib compressed bytes
            compressed = base64.b64decode(encoded_str)

            # Step 2: zlib decompress -> inner base64 string (as bytes)
            inner_b64 = zlib.decompress(compressed).decode("utf-8")[3:-1]

            # Step 3: base64 decode the inner string -> original bytes x
            list_of_slots = ast.literal_eval(inner_b64)
            for slot in list_of_slots:
                slot['metaData'] = self.parse_player_metadata(copy.deepcopy(slot['metaData']))
            return list_of_slots

        except (binascii.Error, zlib.error, ValueError) as e:
            raise ValueError(f"Failed to decode b64(zlib(b64(x))): {e}") from e

    def _build_player_data(self, matches_data: List, stats_data: Dict[int, Dict]) -> Dict[int, Dict]:
        """Enrich stats with avatar/steam info from the raw player list."""
        player_data = {}
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Start with stats (if any)
        for pid, stats in stats_data.items():
            player_data[pid] = stats.copy()
            player_data[pid]["last_updated"] = timestamp

        # Add extra info from the player list (matches_data[2])
        if len(matches_data) > 2 and isinstance(matches_data[2], list):
            for pinfo in matches_data[2]:
                if len(pinfo) > 4:
                    pid = pinfo[1]
                    if pid in player_data:
                        player_data[pid]["steam_id"] = pinfo[11] if len(pinfo) > 11 else ""
                        avatar_str = pinfo[3] if len(pinfo) > 3 else "{}"
                        try:
                            player_data[pid]["avatar_info"] = json.loads(avatar_str)
                        except:
                            player_data[pid]["avatar_info"] = {}
                        player_data[pid]["clan_tag"] = pinfo[5] if len(pinfo) > 5 else ""

        return player_data

    def parse_settings(self, game_settings: str) -> str:
        """
        Extract map name from Age of Empires II game settings string.

        Encoding: base64(zlib(base64(x)))
        Inner format: [4-byte length][key:value]
        Map name is stored under key '11'.

        Args:
            game_settings: Base64 encoded game settings string

        Returns:
            Map filename (e.g., 'megarandom.rms') or 'Unknown'
        """
        # Fix base64 padding
        compressed = base64.b64decode(game_settings)
        inner_b64 = base64.b64decode(zlib.decompress(compressed)).decode("utf-8")
        match = re.search(r'11:([^\x00-\x1f]+\.rms)', inner_b64)
        if match:
            return match.group(1)

        # Backup: search for any .rms file in the text
        match = re.search(r'([^\x00-\x1f]+\.rms)', inner_b64)
        if match:
            return match.group(1)

        return "Unknown"

    def parse_player_metadata(self, player_metadata: str) -> dict | None:
        """
        Parse Age of Empires II player metadata string.

        The metadata uses ASCII control characters as separators.
        Example format: ♥☺0☺7‼ScenarioPlayerIndex☺7♦Team☺3
        Which becomes: [ '', '0', '7', 'ScenarioPlayerIndex', '7', 'Team', '3' ]

        Args:
            player_metadata: The raw metadata string with control characters

        Returns:
            Dictionary with parsed values, or None if input is empty/null
        """
        try:
            # Step 1: base64 decode
            outer = base64.b64decode(player_metadata)

            # Step 3: base64 decode the inner content
            inner = base64.b64decode(outer)

            # Step 4: Convert to string (UTF-8, ignoring errors)
            text = inner.decode('utf-8', errors='ignore')

            # Step 5: Parse the key-value pairs
            # The format appears to use control characters (ASCII < 32) as separators
        except Exception as e:
            print(f"Error decoding KV map: {e}")
            return {}

        if not text:
            return None

        # Replace all control characters (ASCII < 32) with '-'
        cleaned = ''.join('-' if ord(ch) < 32 else ch for ch in text)

        # Collapse multiple consecutive dashes into one
        while '--' in cleaned:
            cleaned = cleaned.replace('--', '-')

        # Split on dashes
        parts = cleaned.split('-')

        # Extract values based on known positions
        return {
            'scenario_player_index': parts[4] if len(parts) > 4 else '',
            'team': parts[6] if len(parts) > 6 else '',
        }

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def clean_player_match_data(self, match_summaries: Dict, player_data: Dict[int, Dict]):

        dirty_json = {
            "match_count": len(match_summaries),
            "matches": match_summaries,
            "player_count": len(player_data),
            "players": player_data
        }
        # Initialize the clean output
        clean_output = {
            "players": []
        }

        # Process matches
        if "matches" in dirty_json:
            match_data = dirty_json["matches"]
            clean_match = {}

            # Extract marked fields for matches
            for key in match_data:
                if key in ["match_id", "lobby_name", "map_name", "game_type"]:
                    clean_match[key] = match_data[key]

            # Process slot_info
            clean_slot_info = []
            for slot in match_data.get("slot_info", []):
                if slot.get("raceID", -1) == -1:
                    continue  # Skip if raceID is -1

                clean_slot = {
                    "profile_id": slot.get("profileInfo.id"),
                    "teamID": slot.get("teamID"),
                    "race_name": CIV_MAPPINGS.get(slot.get("raceID"), {}).get("name", "Unknown"),
                    "scenario_player_index": slot.get("metaData", {}).get("scenario_player_index"),
                }
                clean_slot_info.append(clean_slot)

            clean_match["slot_info"] = clean_slot_info
            clean_output.update(clean_match)

        # Process players
        if "players" in dirty_json:
            for player_id, player_data in dirty_json["players"].items():
                # Check if the player has a valid raceID in slot_info
                valid_player = False
                for slot in dirty_json["matches"].get("slot_info", []):
                    if slot.get("raceID", -1) != -1:
                        valid_player = True
                        break

                if not valid_player:
                    continue  # Skip if no valid raceID

                clean_player = {
                    "alias": player_data.get("alias"),
                    "profile_id": player_id,
                    "country": player_data.get("country"),
                    "elo": player_data.get("elo"),
                    "rank": player_data.get("rank"),
                    "streak": player_data.get("streak"),
                }
                clean_output["players"].append(clean_player)
                for slot in clean_output.get("slot_info", []):
                    for player in clean_output["players"]:
                        if player.get("profile_id") == slot.get("profile_id"):
                            slot.update(player)
                    if slot.get('profile_id') == -1:
                        slot['elo'] = 0
                        slot['rank'] = 0
                        slot['streak'] = 0
                        slot['alias'] = 'AI'
                        slot['country'] = 'In your walls'

                clean_output.pop("players")
        return clean_output
