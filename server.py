import socket
import threading
import os
import datetime
import time
import calendar  # Required for correct GMT parsing to avoid timezone bias

# Configuration
HOST = '127.0.0.1'  # [cite: 21]
PORT = 8080  # [cite: 22]
LOG_FILE = 'server.log'  #

# Thread lock for safe log file access across multiple threads [cite: 54, 67]
log_lock = threading.Lock()


def write_log(client_ip, filename, response_type):
    """
    Records client IP, access time, requested file, and response type[cite: 26, 27].
    """
    access_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{client_ip} | {access_time} | {filename} | {response_type}\n"
    with log_lock:
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry)


def get_http_date(timestamp=None):
    """Returns a GMT formatted date string for HTTP headers."""
    if timestamp is None:
        timestamp = time.time()
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(timestamp))


def parse_http_date(date_str):
    """
    Parses HTTP GMT date string to timestamp.
    Uses calendar.timegm to handle GMT correctly without local timezone bias.
    """
    try:
        return calendar.timegm(time.strptime(date_str, "%a, %d %b %Y %H:%M:%S GMT"))
    except:
        return None


def handle_client(client_socket, client_address):
    """
    Main thread function to process one HTTP request.
    """
    client_ip = client_address[0]
    keep_alive = True

    try:
        while keep_alive:
            client_socket.settimeout(5.0)  # Handle Connection: keep-alive [cite: 59]
            try:
                request_data = client_socket.recv(4096).decode('utf-8')
            except socket.timeout:
                break

            if not request_data:
                break

            lines = request_data.split('\r\n')
            if not lines or not lines[0]:
                break

            # (iii) Parse the request line [cite: 10]
            request_line = lines[0].split()

            # (57) Handle 400 Bad Request
            if len(request_line) != 3:
                send_error(client_socket, "400 Bad Request", "Malformed request", client_ip, "N/A")
                break

            method, filename, version = request_line
            if filename == '/': filename = '/index.html'
            filepath = '.' + filename

            # (59) Parse headers for Connection and Caching
            headers = {}
            for line in lines[1:]:
                if ': ' in line:
                    k, v = line.split(': ', 1)
                    headers[k] = v

            if headers.get('Connection', '').lower() == 'close':
                keep_alive = False

            # (57) Handle 403 Forbidden
            if "server.py" in filename or "server.log" in filename:
                send_error(client_socket, "403 Forbidden", "Access denied", client_ip, filename)
                if not keep_alive: break
                continue

            # (19, 57) Handle 404 File Not Found
            if not os.path.exists(filepath) or not os.path.isfile(filepath):
                send_error(client_socket, "404 Not Found", "File not found", client_ip, filename)
                if not keep_alive: break
                continue

            # (58, 57) Handle Cache Control: Last-Modified and If-Modified-Since
            # Use int() to truncate to second-level precision
            file_mtime = int(os.path.getmtime(filepath))
            last_modified_str = get_http_date(file_mtime)

            if 'If-Modified-Since' in headers:
                if_modified_since_ts = parse_http_date(headers['If-Modified-Since'])

                # Debug output for 304 verification
                print(f"[304 Test] Client TS: {if_modified_since_ts} | File TS: {file_mtime}")

                if if_modified_since_ts is not None and file_mtime <= if_modified_since_ts:
                    print(">>> Result: MATCH! Sending 304 Not Modified.")
                    send_304(client_socket, client_ip, filename, last_modified_str)
                    if not keep_alive: break
                    continue
                else:
                    print(">>> Result: NO MATCH. Sending 200 OK.")

            # (56, 57) Handle 200 OK for GET and HEAD
            if method in ['GET', 'HEAD']:
                with open(filepath, 'rb') as f:
                    content = f.read()

                header = f"HTTP/1.1 200 OK\r\n"
                header += f"Date: {get_http_date()}\r\n"
                header += f"Last-Modified: {last_modified_str}\r\n"
                header += f"Content-Length: {len(content)}\r\n"
                header += f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n"

                if method == 'GET':
                    client_socket.sendall(header.encode('utf-8') + content)
                else:  # HEAD command [cite: 56]
                    client_socket.sendall(header.encode('utf-8'))

                write_log(client_ip, filename, "200 OK")
            else:
                send_error(client_socket, "400 Bad Request", "Method not supported", client_ip, filename)

            if not keep_alive: break

    except Exception as e:
        print(f"Connection error: {e}")
    finally:
        client_socket.close()


def send_error(sock, status, msg, ip, filename):
    """Sends error responses (400, 403, 404)[cite: 19, 57]."""
    body = f"<html><body><h1>{status}</h1><p>{msg}</p></body></html>"
    header = f"HTTP/1.1 {status}\r\n"
    header += f"Date: {get_http_date()}\r\n"
    header += f"Content-Length: {len(body)}\r\n"
    header += "Connection: close\r\n\r\n"
    sock.sendall(header.encode('utf-8') + body.encode('utf-8'))
    write_log(ip, filename, status)


def send_304(sock, ip, filename, last_mod):
    """Sends 304 Not Modified response[cite: 57, 58]."""
    header = f"HTTP/1.1 304 Not Modified\r\n"
    header += f"Date: {get_http_date()}\r\n"
    header += f"Last-Modified: {last_mod}\r\n"
    header += "Connection: keep-alive\r\n\r\n"
    sock.sendall(header.encode('utf-8'))
    write_log(ip, filename, "304 Not Modified")


def start_server():
    """Starts the multi-threaded socket server[cite: 8, 30]."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind((HOST, PORT))  # [cite: 21]
        server_socket.listen(5)
        print(f"Multi-threaded Web Server running on http://{HOST}:{PORT}")

        while True:
            # (12) Create connection socket
            client_sock, addr = server_socket.accept()
            # (54) Create one thread per request
            threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True).start()

    except KeyboardInterrupt:
        print("\nServer stopping...")
    finally:
        server_socket.close()


if __name__ == '__main__':
    start_server()