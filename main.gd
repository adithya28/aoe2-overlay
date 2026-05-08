extends Control

# -------------------------------------------------------------
# WebSocket peer
# -------------------------------------------------------------
var ws: WebSocketPeer = null
var ws_connected: bool = false
var reconnect_timer: float = 0.0

# -------------------------------------------------------------
# Fallback timer – reload JSON when WebSocket is down
# -------------------------------------------------------------
var fallback_timer: float = 0.0
const FALLBACK_INTERVAL: float = 5.0   # seconds

# -------------------------------------------------------------
# Player colours (same as HTML overlay)
# -------------------------------------------------------------
const PLAYER_COLORS: Array[Color] = [
	Color("#0066cc"), Color("#dc3232"), Color("#009600"), Color("#ffd700"),
	Color("#009688"), Color("#a050c8"), Color("#a0a0a0"), Color("#ff8c00")
]

# -------------------------------------------------------------
# UI references (for clearing)
# -------------------------------------------------------------
var main_container: Control = null

# -------------------------------------------------------------
# Ready – connect WebSocket and load fallback JSON
# -------------------------------------------------------------
func _ready():
	# Transparent background for the root control
	self_modulate = Color(1, 1, 1, 0)
	
	# Initiate WebSocket connection
	connect_websocket()
	
	# Show existing data immediately (if player_stats.json exists)
	reload_json()

# -------------------------------------------------------------
# WebSocket connection
# -------------------------------------------------------------
func connect_websocket():
	ws = WebSocketPeer.new()
	var err = ws.connect_to_url("ws://localhost:8765")
	if err != OK:
		print("WebSocket connect error: ", err)
		ws = null
	else:
		print("Connecting to ws://localhost:8765...")

# -------------------------------------------------------------
# Process – poll WebSocket every frame, handle fallback polling
# -------------------------------------------------------------
func _process(delta: float) -> void:
	# WebSocket handling
	if ws:
		ws.poll()
		var state = ws.get_ready_state()
		
		if state == WebSocketPeer.STATE_OPEN:
			if not ws_connected:
				ws_connected = true
				print("WS connected")
				fallback_timer = 0.0   # stop fallback
			# Read messages
			while ws.get_available_packet_count() > 0:
				var pkt = ws.get_packet()
				var message = pkt.get_string_from_utf8()
				var json = JSON.parse_string(message)
				if json != null:
					render_match(json)
		
		elif state == WebSocketPeer.STATE_CLOSED:
			if ws_connected:
				ws_connected = false
				ws = null
				print("WS disconnected")
				reconnect_timer = 2.0
			elif reconnect_timer <= 0:
				reconnect_timer = 2.0
	else:
		# WebSocket not active – handle reconnection timer
		if reconnect_timer > 0:
			reconnect_timer -= delta
			if reconnect_timer <= 0:
				connect_websocket()
	
	# Fallback polling: reload JSON file periodically when WebSocket is not connected
	if not ws_connected:
		fallback_timer += delta
		if fallback_timer >= FALLBACK_INTERVAL:
			fallback_timer = 0.0
			reload_json()

# -------------------------------------------------------------
# Reload local JSON (fallback)
# -------------------------------------------------------------
func reload_json():
	var file = FileAccess.open("../../player_stats.json", FileAccess.READ)
	if file:
		var text = file.get_as_text()
		var json = JSON.parse_string(text)
		if json != null:
			render_match(json)

# -------------------------------------------------------------
# Render the match data
# -------------------------------------------------------------
func render_match(data: Dictionary):
	clear_ui()
	
	if not data.has("slot_info") or data.slot_info.is_empty():
		return
	
	var teams: Dictionary = {}
	for player in data.slot_info:
		var tid = player.get("teamID", -1)
		if not teams.has(tid):
			teams[tid] = []
		teams[tid].append(player)
	
	var team_ids: Array = teams.keys()
	team_ids.sort()
	
	# Main vertical box (title + team row)
	var main_vbox = VBoxContainer.new()
	main_vbox.alignment = BoxContainer.ALIGNMENT_CENTER
	main_vbox.add_theme_constant_override("separation", 4)
	add_child(main_vbox)
	main_container = main_vbox
	
	# Title
	var title = Label.new()
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.text = "%s  •  %s  (type %s)" % [
		data.get("lobby_name", ""),
		data.get("map_name", "Unknown"),
		data.get("game_type", "?")
	]
	title.add_theme_font_size_override("font_size", 10)
	title.modulate = Color.BLACK
	var title_bg = StyleBoxFlat.new()
	title_bg.bg_color = Color(1, 1, 1, 0.4)
	title_bg.set_content_margin_all(4)
	title.add_theme_stylebox_override("normal", title_bg)
	main_vbox.add_child(title)
	
	# Team row (HBox)
	var team_row = HBoxContainer.new()
	team_row.alignment = BoxContainer.ALIGNMENT_CENTER
	team_row.add_theme_constant_override("separation", 8)
	main_vbox.add_child(team_row)
	
	for i in range(team_ids.size()):
		var tid = team_ids[i]
		var team_players = teams[tid]
		
		# Team column (VBox)
		var team_col = VBoxContainer.new()
		team_col.alignment = BoxContainer.ALIGNMENT_CENTER
		team_col.add_theme_constant_override("separation", 2)
		team_row.add_child(team_col)
		
		# Team badge
		var badge = Label.new()
		badge.text = "Team %d" % tid
		badge.add_theme_font_size_override("font_size", 7)
		badge.modulate = Color.BLACK
		var badge_bg = StyleBoxFlat.new()
		badge_bg.bg_color = Color(0, 0, 0, 0.15)
		badge_bg.set_content_margin_all(2)
		badge.add_theme_stylebox_override("normal", badge_bg)
		team_col.add_child(badge)
		
		# Players
		for player in team_players:
			var card = create_player_card(player)
			team_col.add_child(card)
		
		# VS divider between teams (except last)
		if i < team_ids.size() - 1:
			var vs = Label.new()
			vs.text = "VS"
			vs.add_theme_font_size_override("font_size", 9)
			vs.modulate = Color.BLACK
			team_row.add_child(vs)
	
	# Resize window to content (after next frame)
	call_deferred("resize_window_to_content")

# -------------------------------------------------------------
# Create a single player card (HBox)
# -------------------------------------------------------------
func create_player_card(player: Dictionary) -> HBoxContainer:
	var card = HBoxContainer.new()
	card.add_theme_constant_override("separation", 3)
	
	# Country flag
	var country = player.get("country", "").to_lower()
	var flag_tex = load_texture_if_exists("res://Flags/%s.png" % country, 16, 10)
	if flag_tex:
		var flag_rect = TextureRect.new()
		flag_rect.texture = flag_tex
		flag_rect.expand_mode = TextureRect.EXPAND_FIT_WIDTH_PROPORTIONAL
		flag_rect.custom_minimum_size = Vector2(16, 10)
		card.add_child(flag_rect)
	
	# Name with colour background
	var alias = player.get("alias", "Unknown")
	var name_label = Label.new()
	name_label.text = alias
	name_label.add_theme_font_size_override("font_size", 8)
	var color_idx = int(player.get("scenario_player_index", -1))
	if color_idx >= 0 and color_idx < PLAYER_COLORS.size():
		var bg_color = PLAYER_COLORS[color_idx]
		name_label.modulate = Color.WHITE if (bg_color.v < 0.5) else Color.BLACK
		var bg = StyleBoxFlat.new()
		bg.bg_color = bg_color
		bg.set_content_margin_all(2)
		name_label.add_theme_stylebox_override("normal", bg)
	else:
		name_label.modulate = Color.BLACK
	card.add_child(name_label)
	
	# ELO
	var elo = player.get("elo", "---")
	var elo_label = Label.new()
	elo_label.text = str(elo)
	elo_label.add_theme_font_size_override("font_size", 7)
	elo_label.modulate = Color.BLACK
	card.add_child(elo_label)
	
	# Civ icon + name
	var race = player.get("race_name", "")
	if race != "":
		var civ_tex = load_texture_if_exists("res://Civs/%s_AoE2.webp" % race, 14, 14)
		if civ_tex:
			var civ_rect = TextureRect.new()
			civ_rect.texture = civ_tex
			civ_rect.expand_mode = TextureRect.EXPAND_FIT_WIDTH_PROPORTIONAL
			civ_rect.custom_minimum_size = Vector2(14, 14)
			card.add_child(civ_rect)
		var civ_label = Label.new()
		civ_label.text = race
		civ_label.add_theme_font_size_override("font_size", 7)
		civ_label.modulate = Color.BLACK
		card.add_child(civ_label)
	
	return card

# -------------------------------------------------------------
# Helper: load a texture and resize, return null if missing
# -------------------------------------------------------------
func load_texture_if_exists(path: String, width: float, height: float) -> Texture2D:
	if not FileAccess.file_exists(path):
		return null
	var img = Image.new()
	var err = img.load(path)
	if err != OK:
		return null
	img.resize(int(width), int(height), Image.INTERPOLATE_LANCZOS)
	return ImageTexture.create_from_image(img)

# -------------------------------------------------------------
# Clear all previous UI elements
# -------------------------------------------------------------
func clear_ui():
	for child in get_children():
		child.queue_free()
	main_container = null

# -------------------------------------------------------------
# Resize window to exactly fit the content
# -------------------------------------------------------------
func resize_window_to_content():
	# After all children are placed, the root control's rect reflects total size
	var content_size = self.size
	# Add a tiny padding
	DisplayServer.window_set_size(Vector2i(content_size) + Vector2i(20, 10))
