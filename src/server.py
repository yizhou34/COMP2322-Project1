import socket
import threading
import os
import datetime
import time
import calendar

# --- Core Path Configuration ---
# Get the absolute path of the directory where server.py is located (src/)
current_script_dir = os.path.dirname(os.path.abspath(__file__))
# Get the project root directory (one level up from src/)
BASE_DIR = os.path.dirname(current_script_dir)

# Ensure the server can always find the 'test_files' folder in the root directory
TEST_FILES_DIR = os.path.join(BASE_DIR, 'test_files')

# Ensure the 'server.log' is always created in the project root directory
LOG_FILE = os.path.join(BASE_DIR, 'server.log')

# Thread lock to ensure thread-safe writing to the shared log file
log_lock = threading.Lock()


def write_log(client_ip, filename, response_type):
    """
    Records request statistics: IP, access time, filename, and response type.
    Format: IP | Time | Filename | Status
    """
    access_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{client_ip} | {access_time} | {filename} | {response_type}\n"
    with log_lock:
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry)


def get_http_date(timestamp=None):
    """Returns a GMT formatted date string for HTTP headers (e.g., Last-Modified)."""
    if timestamp is None:
        timestamp = time.time()
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(timestamp))


def parse_http_date(date_str):
    """
    Parses HTTP GMT date string into a timestamp.
    Uses calendar.timegm to handle GMT correctly and avoid timezone bias.
    """
    try:
        return calendar.timegm(time.strptime(date_str, "%a, %d %b %Y %H:%M:%S GMT"))
    except:
        return None


def handle_client(client_socket, client_address):
    """
    Handles a single client connection in an independent thread.
    Processes HTTP requests and generates appropriate responses.
    """
    client_ip = client_address[0]
    keep_alive = True

    try:
        while keep_alive:
            # Set a timeout for persistent connections (Connection: keep-alive)
            client_socket.settimeout(5.0)
            try:
                request_data = client_socket.recv(4096).decode('utf-8')
            except socket.timeout:
                break  # Close connection if idle for too long

            if not request_data:
                break

            lines = request_data.split('\r\n')
            if not lines or not lines[0]:
                break

            # Parse the request line (e.g., GET /index.html HTTP/1.1)
            request_line = lines[0].split()

            # Handle 400 Bad Request: malformed request line
            if len(request_line) != 3:
                send_error(client_socket, "400 Bad Request", "Malformed request line", client_ip, "N/A")
                break

            method, filename, version = request_line

            # Default to index.html if root directory is requested
            if filename == '/':
                filename = '/index.html'

            # Construct the absolute path to the requested file
            # lstrip('/') removes the leading slash to avoid os.path.join issues
            filepath = os.path.join(TEST_FILES_DIR, filename.lstrip('/'))

            # Parse HTTP headers into a dictionary
            headers = {}
            for line in lines[1:]:
                if ': ' in line:
                    k, v = line.split(': ', 1)
                    headers[k] = v

            # Manage persistent connection state
            if headers.get('Connection', '').lower() == 'close':
                keep_alive = False

            # Handle 403 Forbidden: prevent access to source code or logs
            if "server.py" in filename or "server.log" in filename:
                send_error(client_socket, "403 Forbidden", "Access to this file is forbidden.", client_ip, filename)
                if not keep_alive: break
                continue

            # Handle 404 Not Found: file does not exist in test_files directory
            if not os.path.exists(filepath) or not os.path.isfile(filepath):
                send_error(client_socket, "404 Not Found", "The requested file was not found.", client_ip, filename)
                if not keep_alive: break
                continue

            # Handle 304 Not Modified (Cache Control logic)
            # Use int() for second-level precision to match HTTP date format
            file_mtime = int(os.path.getmtime(filepath))
            last_modified_str = get_http_date(file_mtime)

            if 'If-Modified-Since' in headers:
                if_modified_since_ts = parse_http_date(headers['If-Modified-Since'])
                # If file hasn't changed since the client's cached version, send 304
                if if_modified_since_ts is not None and file_mtime <= if_modified_since_ts:
                    send_304(client_socket, client_ip, filename, last_modified_str)
                    if not keep_alive: break
                    continue

            # Handle 200 OK for GET and HEAD methods
            if method in ['GET', 'HEAD']:
                # Basic MIME type identification
                content_type = "text/html"
                if filename.lower().endswith((".jpg", ".jpeg")):
                    content_type = "image/jpeg"
                elif filename.lower().endswith(".png"):
                    content_type = "image/png"

                with open(filepath, 'rb') as f:
                    content = f.read()

                header = f"HTTP/1.1 200 OK\r\n"
                header += f"Date: {get_http_date()}\r\n"
                header += f"Content-Type: {content_type}\r\n"
                header += f"Last-Modified: {last_modified_str}\r\n"
                header += f"Content-Length: {len(content)}\r\n"
                header += f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n"

                if method == 'GET':
                    client_socket.sendall(header.encode('utf-8') + content)
                else:  # HEAD command only sends headers
                    client_socket.sendall(header.encode('utf-8'))

                write_log(client_ip, filename, "200 OK")
            else:
                # Handle 400 Bad Request: unsupported method
                send_error(client_socket, "400 Bad Request", "Method not supported", client_ip, filename)

            if not keep_alive: break

    except Exception as e:
        # Silently ignore errors during connection handling to prevent server crash
        pass
    finally:
        client_socket.close()


def send_error(sock, status, msg, ip, filename):
    """Sends standardized HTTP error responses (400, 403, 404)."""
    body = f"<html><body><h1>{status}</h1><p>{msg}</p></body></html>"
    header = f"HTTP/1.1 {status}\r\n"
    header += f"Date: {get_http_date()}\r\n"
    header += f"Content-Type: text/html\r\n"
    header += f"Content-Length: {len(body)}\r\n"
    header += "Connection: close\r\n\r\n"
    sock.sendall(header.encode('utf-8') + body.encode('utf-8'))
    write_log(ip, filename, status)


def send_304(sock, ip, filename, last_mod):
    """Sends 304 Not Modified response (header only)."""
    header = f"HTTP/1.1 304 Not Modified\r\n"
    header += f"Date: {get_http_date()}\r\n"
    header += f"Last-Modified: {last_mod}\r\n"
    header += "Connection: keep-alive\r\n\r\n"
    sock.sendall(header.encode('utf-8'))
    write_log(ip, filename, "304 Not Modified")


def start_server():
    """Initializes and starts the multi-threaded socket server."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Set option to reuse address to avoid 'Address already in use' errors
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        # Bind to localhost on port 8080
        server_socket.bind(('127.0.0.1', 8080))
        server_socket.listen(5)
        print(f"--- Multi-threaded Web Server Started ---")
        print(f"Project Root: {BASE_DIR}")
        print(f"Test Files Directory: {TEST_FILES_DIR}")
        print(f"Server Log Location: {LOG_FILE}")
        print(f"Server Listening on http://127.0.0.1:8080")

        while True:
            # Accept new incoming TCP connection
            client_sock, addr = server_socket.accept()
            # Assign a new daemon thread to handle the client
            threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True).start()

    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
    finally:
        server_socket.close()


if __name__ == '__main__':
    start_server()