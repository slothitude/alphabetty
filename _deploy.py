"""Deploy Alphabetty to Lappy via paramiko."""
import paramiko, os, sys, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.33", username="aaron", password="T0b1@n7243")

sftp = ssh.open_sftp()

remote_base = "C:/Users/aaron/Desktop/alphabetty"
local_base = "C:/Users/aaron/Desktop/alphabetty"

files_to_sync = [
    "app.py",
    "config.py",
    "models/conversation.py",
    "models/space.py",
    "models/graph.py",
    "models/__init__.py",
    "core/graph.py",
    "core/__init__.py",
    "api/chat.py",
    "api/graph.py",
    "api/__init__.py",
    "static/js/graph.js",
    "static/index.html",
    "static/css/app.css",
    "static/js/app.js",
    "static/js/chat.js",
    "static/js/search.js",
    "static/js/dom-panel.js",
    "static/js/research.js",
    "static/js/spaces.js",
    "static/js/export.js",
    "Dockerfile",
    "docker-compose.yml",
    "entrypoint.sh",
    "requirements.txt",
    "run.py",
    "core/llm.py",
    "core/cdp_bridge.py",
    "core/chrome.py",
    "core/searxng.py",
    "core/content_extractor.py",
    "core/source_citer.py",
    "core/research_engine.py",
    "core/file_analyzer.py",
    "api/search.py",
    "api/cdp.py",
    "api/research.py",
    "api/files.py",
    "api/images.py",
    "api/spaces.py",
    "api/export.py",
    "api/agent.py",
    ".dockerignore",
    "mcp_server.py",
    "core/auth.py",
    "core/events.py",
    "core/macro.py",
    "core/n8n.py",
    "core/providers.py",
    "core/media.py",
    "core/swarm.py",
    "api/auth.py",
    "api/workflows.py",
    "api/extension.py",
    "api/download.py",
    "api/share.py",
    "api/models.py",
    "api/swarm.py",
    "api/media.py",
    "api/tools.py",
    "api/signin.py",
    "models/user.py",
    "static/js/dashboard.js",
]

synced = 0
errors = []
for f in files_to_sync:
    local_path = os.path.join(local_base, f.replace("/", os.sep))
    remote_path = remote_base + "/" + f

    if not os.path.exists(local_path):
        print(f"SKIP (not found): {f}")
        continue

    # Ensure remote directory exists
    remote_dir = os.path.dirname(f)
    if remote_dir:
        full_dir = remote_base + "/" + remote_dir
        try:
            sftp.stat(full_dir)
        except FileNotFoundError:
            # Create dirs recursively
            parts = full_dir.split("/")
            for i in range(1, len(parts) + 1):
                try:
                    sftp.stat("/".join(parts[:i]))
                except FileNotFoundError:
                    sftp.mkdir("/".join(parts[:i]))

    try:
        sftp.put(local_path, remote_path)
        synced += 1
        print(f"OK: {f}")
    except Exception as e:
        errors.append(f"{f}: {e}")
        print(f"FAIL: {f}: {e}")

print(f"\nSynced {synced} files, {len(errors)} errors")

# Make entrypoint.sh executable
try:
    sftp.chmod(remote_base + "/entrypoint.sh", 0o755)
except Exception:
    pass

sftp.close()

if errors:
    print("Aborting due to sync errors")
    ssh.close()
    sys.exit(1)

# Stop existing container
print("\nStopping container...")
stdin, stdout, stderr = ssh.exec_command(f"cd {remote_base} && docker compose down", timeout=30)
print(stdout.read().decode())
print(stderr.read().decode())

# Build
print("Building image...")
stdin, stdout, stderr = ssh.exec_command(f"cd {remote_base} && docker compose build --no-cache", timeout=600)
for line in iter(stdout.readline, ""):
    if line:
        sys.stdout.buffer.write((line.rstrip() + "\n").encode("utf-8", errors="replace"))
err = stderr.read().decode()
if err:
    # Print last 1000 chars of stderr
    print("STDERR:", err[-1000:] if len(err) > 1000 else err)

# Start
print("\nStarting container...")
stdin, stdout, stderr = ssh.exec_command(f"cd {remote_base} && docker compose up -d", timeout=60)
print(stdout.read().decode())
print(stderr.read().decode())

time.sleep(5)

# Check status
stdin, stdout, stderr = ssh.exec_command(f"cd {remote_base} && docker compose ps", timeout=15)
print("Container status:")
print(stdout.read().decode())

# Check logs briefly
stdin, stdout, stderr = ssh.exec_command(f"cd {remote_base} && docker compose logs --tail=30", timeout=15)
print("Recent logs:")
print(stdout.read().decode())

ssh.close()
print("Done!")
