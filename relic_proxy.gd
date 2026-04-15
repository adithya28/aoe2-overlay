extends Node

# ============================================================
# RelicLinkProxy.gd – Godot port of the Python proxy
# ============================================================
#
# Dependencies:
# - GodotSteam (https://github.com/GodotSteam/GodotSteam)
# - Two JSON config files in user:// :
#     config.json        -> { "steam_account": "...", "steam_password": "..." }
#     api_keys.json      -> { "key1": "...", "key2": "..." }
#     steam_secrets.json -> your SteamAuthenticator secrets
#
# The script:
# 1. Logs into Steam (with 2FA using GodotSteam).
# 2. Periodically obtains an encrypted app ticket for Age of Empires II (AppID 813780).
# 3. Uses that ticket to obtain a Relic session from aoe-api.worldsedgelink.com.
# 4. Runs a minimal HTTP server on port 5000 that forwards incoming requests to the Relic API,
#    injecting the session ID and other required parameters.
# ============================================================

const APPID: int = 813780
const APPID_STR: String = "age2"
const RELIC_HOST: String = "https://aoe-api.worldsedgelink.com"
const LISTEN_PORT: int = 5000

# ------------------------------------------------------------------
# State variables
# ------------------------------------------------------------------
var steam: SteamClient          # from GodotSteam
var steam_logged_in: bool = false
var steam_id_64: String = ""
var steam_user_name: String = ""

var app_ticket: String = ""      # base64 encoded encrypted app ticket
var app_ticket_timestamp: int = 0

var relic_session_id: String = ""
var relic_session_timestamp: int = 0

var api_keys: Array = []

var http_client: HTTPRequest     # for outgoing Relic API calls
var tcp_server: TCPServer        # for incoming proxy requests
var update_timer: Timer

# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------
func _ready():
	_load_config()
	_initialize_steam()
	_setup_http_client()
	_setup_tcp_server()
	_setup_timer()
	print("[RelicLinkProxy] Started. Listening on port ", LISTEN_PORT)

func _exit_tree():
	if tcp_server:
		tcp_server.stop()
	if http_client:
		http_client.queue_free()

# ------------------------------------------------------------------
# Configuration loading (replaces .env)
# ------------------------------------------------------------------
func _load_config():
	var config_file = FileAccess.open("user://config.json", FileAccess.READ)
	if config_file:
		var config = JSON.parse_string(config_file.get_as_text())
		steam_account_name = config.get("steam_account", "")
		steam_password = config.get("steam_password", "")
		config_file.close()
	else:
		push_error("Missing user://config.json")
		get_tree().quit()

	var api_file = FileAccess.open("user://api_keys.json", FileAccess.READ)
	if api_file:
		var keys_dict = JSON.parse_string(api_file.get_as_text())
		api_keys = keys_dict.values()
		api_file.close()
	else:
		push_error("Missing user://api_keys.json")
		get_tree().quit()

	# Steam secrets are used directly by GodotSteam's authenticator;
	# they must be loaded via GodotSteam's set_secrets() method.

# ------------------------------------------------------------------
# Steam initialization (using GodotSteam)
# ------------------------------------------------------------------
func _initialize_steam():
	steam = SteamClient.new()
	add_child(steam)

	# Connect signals
	steam.connect("steam_connected", Callable(self, "_on_steam_connected"))
	steam.connect("steam_disconnected", Callable(self, "_on_steam_disconnected"))
	steam.connect("steam_login_result", Callable(self, "_on_steam_login_result"))
	steam.connect("encrypted_app_ticket_response", Callable(self, "_on_encrypted_app_ticket"))

	# Initialize with AppID (Age2's Steam AppID)
	var init_result = steam.steamInitEx(APPID)
	if init_result != 1:
		push_error("Steam initialization failed. Is Steam running?")
		get_tree().quit()

# Called when Steam is connected to the client
func _on_steam_connected():
	print("[Steam] Connected. Logging in...")
	# Load Steam secrets for 2FA
	var secrets_file = FileAccess.open("user://steam_secrets.json", FileAccess.READ)
	if secrets_file:
		var secrets = JSON.parse_string(secrets_file.get_as_text())
		steam.set_steam_authenticator_secrets(secrets)   # GodotSteam method
		secrets_file.close()
	else:
		push_error("Missing steam_secrets.json")
		get_tree().quit()

	# Trigger login (with 2FA code generated internally by GodotSteam)
	steam.steam_login(steam_account_name, steam_password)

func _on_steam_disconnected():
	print("[Steam] Disconnected")
	steam_logged_in = false

func _on_steam_login_result(result: int, steam_id: int):
	if result == Steam.EResult.OK:
		steam_logged_in = true
		steam_id_64 = str(steam_id)
		steam_user_name = steam.getFriendPersonaName(steam_id)
		print("[Steam] Login successful. User: ", steam_user_name)
		# Start the update cycle
		_on_update_timer_timeout()
	else:
		push_error("[Steam] Login failed with result: ", result)
		get_tree().quit()

# ------------------------------------------------------------------
# Encrypted App Ticket (GodotSteam)
# ------------------------------------------------------------------
func _request_encrypted_app_ticket():
	if not steam_logged_in:
		return
	# Request an encrypted app ticket with userdata = "RLINK"
	steam.requestEncryptedAppTicket("RLINK".to_utf8_buffer())

func _on_encrypted_app_ticket(result: int):
	if result != Steam.EResult.OK:
		push_error("[Steam] Failed to get encrypted app ticket")
		return

	var ticket_bytes: PackedByteArray = steam.getEncryptedAppTicket()
	app_ticket = Marshalls.raw_to_base64(ticket_bytes)
	app_ticket_timestamp = Time.get_unix_time_from_system()
	print("[Steam] App ticket refreshed")

# ------------------------------------------------------------------
# Relic Session Management
# ------------------------------------------------------------------
func _setup_http_client():
	http_client = HTTPRequest.new()
	add_child(http_client)
	http_client.connect("request_completed", Callable(self, "_on_relic_response"))

func _refresh_relic_session():
	if app_ticket.is_empty() or not steam_logged_in:
		return

	var url = RELIC_HOST + "/game/login/platformlogin"
	var body = {
		"accountType": "STEAM",
		"activeMatchId": "-1",
		"alias": steam_user_name,
		"appID": str(APPID),
		"auth": app_ticket,
		"callNum": "0",
		"clientLibVersion": "169",
		"connect_id": "",
		"country": "US",
		"installationType": "windows",
		"language": "en",
		"lastCallTime": "33072262",
		"macAddress": "57-4F-4C-4F-4C-4F",
		"majorVersion": "4.0.0",
		"minorVersion": "0",
		"platformUserID": steam_id_64,
		"startGameToken": "",
		"syncHash": "[3705476802, 2905248376]",
		"timeoutOverride": "0",
		"title": APPID_STR
	}

	var headers = ["Content-Type: application/x-www-form-urlencoded"]
	var query_string = http_client.new_client().query_string_from_dict(body)
	http_client.request(url, headers, HTTPClient.METHOD_POST, query_string)

func _on_relic_response(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray):
	if response_code != 200:
		push_error("[Relic] Login failed with HTTP ", response_code)
		return

	var text = body.get_string_from_utf8()
	if steam_id_64 in text:
		var json = JSON.new()
		if json.parse(text) == OK:
			var data = json.get_data()
			if data is Array and data.size() > 1:
				relic_session_id = data[1]
				relic_session_timestamp = Time.get_unix_time_from_system()
				print("[Relic] Session refreshed: ", relic_session_id)
	else:
		push_error("[Relic] Login response did not contain expected data")

# ------------------------------------------------------------------
# Periodic Updates (Timer)
# ------------------------------------------------------------------
func _setup_timer():
	update_timer = Timer.new()
	update_timer.wait_time = 10.0
	update_timer.autostart = true
	update_timer.connect("timeout", Callable(self, "_on_update_timer_timeout"))
	add_child(update_timer)

func _on_update_timer_timeout():
	if not steam_logged_in:
		return

	# Refresh app ticket if older than 45 minutes (2700 seconds)
	var now = Time.get_unix_time_from_system()
	if app_ticket.is_empty() or now > app_ticket_timestamp + 2700:
		_request_encrypted_app_ticket()

	# Refresh relic session if older than 200 seconds
	if relic_session_id.is_empty() or now > relic_session_timestamp + 200:
		if not app_ticket.is_empty():
			_refresh_relic_session()

# ------------------------------------------------------------------
# HTTP Proxy Server (TCPServer + manual HTTP parsing)
# ------------------------------------------------------------------
func _setup_tcp_server():
	tcp_server = TCPServer.new()
	if tcp_server.listen(LISTEN_PORT) != OK:
		push_error("Failed to start TCP server on port ", LISTEN_PORT)
		get_tree().quit()
	# Poll for new connections every frame
	set_process(true)

func _process(_delta):
	if not tcp_server.is_listening():
		return
	if tcp_server.is_connection_available():
		var peer = tcp_server.take_connection()
		_handle_client(peer)

# Very minimal HTTP request parser
func _handle_client(peer: StreamPeerTCP):
	var request_data = peer.get_utf8_string(4096)
	if request_data.is_empty():
		peer.disconnect_from_host()
		return

	var lines = request_data.split("\r\n")
	if lines.size() < 1:
		peer.disconnect_from_host()
		return

	var request_line = lines[0].split(" ")
	if request_line.size() < 2:
		peer.disconnect_from_host()
		return

	var method = request_line[0]
	var path = request_line[1]

	# Headers parsing (simple)
	var headers = {}
	var body_start = -1
	for i in range(1, lines.size()):
		var line = lines[i]
		if line == "":
			body_start = i + 1
			break
		var parts = line.split(": ", true, 1)
		if parts.size() == 2:
			headers[parts[0].to_lower()] = parts[1]

	# Extract body if any
	var body = ""
	if body_start != -1 and body_start < lines.size():
		body = "\r\n".join(lines.slice(body_start))

	# Handle routes
	if path == "/relic":
		_send_dot_response(peer)
	elif path.begins_with("/relic/"):
		var endpoint = path.substr(7)  # remove "/relic/"
		_forward_request(peer, method, endpoint, headers, body)
	else:
		_send_response(peer, 404, "Not Found")

	peer.disconnect_from_host()

func _send_dot_response(peer: StreamPeerTCP):
	var data = {}
	if not app_ticket.is_empty():
		data["encrypted_app_token"] = {
			"last_update": app_ticket_timestamp,
			"utc_string": Time.get_datetime_string_from_unix_time(app_ticket_timestamp, true)
		}
	if not relic_session_id.is_empty():
		data["relic_session"] = {
			"last_update": relic_session_timestamp,
			"utc_string": Time.get_datetime_string_from_unix_time(relic_session_timestamp, true)
		}
	var json_str = JSON.stringify(data)
	_send_json_response(peer, json_str)

func _forward_request(peer: StreamPeerTCP, method: String, endpoint: String, headers: Dictionary, body: String):
	if relic_session_id.is_empty():
		_send_response(peer, 503, "Relic session not ready")
		return

	# Filter out headers that should not be forwarded
	var excluded = ["host", "user-agent", "content-length", "connection", "api_key"]
	var forward_headers = []
	for key in headers.keys():
		if key not in excluded:
			forward_headers.append(key.capitalize() + ": " + headers[key])

	# Add or replace API key
	var api_key_to_use = headers.get("api_key", api_keys[0] if api_keys.size() > 0 else "")
	if api_key_to_use in api_keys:
		forward_headers.append("api_key: " + api_key_to_use)

	# Build URL with query parameters for GET, or body for POST
	var url = RELIC_HOST + "/" + endpoint
	var query_dict = {}
	if method == "GET":
		query_dict = _parse_query_string_from_path(endpoint)  # simple, see below
		query_dict["callNum"] = 0
		query_dict["connect_id"] = relic_session_id
		query_dict["lastCallTime"] = Time.get_unix_time_from_system()
		query_dict["sessionID"] = relic_session_id
		url = RELIC_HOST + "/" + endpoint + "?" + http_client.new_client().query_string_from_dict(query_dict)

	elif method == "POST":
		var post_dict = _parse_urlencoded(body)
		post_dict["callNum"] = 0
		post_dict["connect_id"] = relic_session_id
		post_dict["lastCallTime"] = 33072262
		post_dict["sessionID"] = relic_session_id
		body = http_client.new_client().query_string_from_dict(post_dict)
		forward_headers.append("Content-Type: application/x-www-form-urlencoded")
		forward_headers.append("Content-Length: " + str(body.length()))

	else:
		_send_response(peer, 405, "Method Not Allowed")
		return

	# Perform request using HTTPRequest node (asynchronous)
	# We need to capture the response and send it back to the original client.
	# For simplicity, we use a one‑off HTTPRequest and wait for its signal.
	var req = HTTPRequest.new()
	add_child(req)
	req.connect("request_completed", Callable(self, "_on_forward_complete").bind(peer))
	var error = req.request(url, forward_headers, 
		HTTPClient.METHOD_GET if method == "GET" else HTTPClient.METHOD_POST, 
		body if method == "POST" else "")
	if error != OK:
		_send_response(peer, 500, "Internal proxy error")
		req.queue_free()

func _on_forward_complete(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray, peer: StreamPeerTCP):
	var response_str = "HTTP/1.1 " + str(response_code) + " OK\r\n"
	response_str += "Content-Length: " + str(body.size()) + "\r\n"
	response_str += "\r\n"
	response_str += body.get_string_from_utf8()
	peer.put_data(response_str.to_utf8_buffer())
	# Clean up the temporary request node
	var req = get_node(peer.get_path())  # careful: we attached to the request signal
	req.queue_free()

func _send_json_response(peer: StreamPeerTCP, json_str: String):
	var response = "HTTP/1.1 200 OK\r\n"
	response += "Content-Type: application/json\r\n"
	response += "Content-Length: " + str(json_str.length()) + "\r\n"
	response += "\r\n"
	response += json_str
	peer.put_data(response.to_utf8_buffer())

func _send_response(peer: StreamPeerTCP, code: int, message: String):
	var response = "HTTP/1.1 " + str(code) + " " + message + "\r\n"
	response += "Content-Length: " + str(message.length()) + "\r\n"
	response += "\r\n"
	response += message
	peer.put_data(response.to_utf8_buffer())

# Helper: parse query string from a full path (naive)
func _parse_query_string_from_path(path: String) -> Dictionary:
	var qs_index = path.find("?")
	if qs_index == -1:
		return {}
	var qs = path.substr(qs_index + 1)
	var dict = {}
	for pair in qs.split("&"):
		var parts = pair.split("=")
		if parts.size() == 2:
			dict[parts[0]] = parts[1].uri_decode()
	return dict

# Helper: parse application/x-www-form-urlencoded body
func _parse_urlencoded(body: String) -> Dictionary:
	var dict = {}
	for pair in body.split("&"):
		var parts = pair.split("=")
		if parts.size() == 2:
			dict[parts[0]] = parts[1].uri_decode()
	return dict
