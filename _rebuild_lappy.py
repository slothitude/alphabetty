"""Rebuild Alphabetty Docker on Lappy via schtasks."""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.33", username="aaron", password="T0b1@n7243")

sftp = ssh.open_sftp()

# Write build script
build_script = r"""@echo off
cd C:\Users\aaron\Desktop\alphabetty
docker compose down
docker compose build --no-cache
if %ERRORLEVEL% NEQ 0 (
    echo BUILD_FAILED > C:\Users\aaron\Desktop\alphabetty\build_status.txt
    exit /b 1
)
docker compose up -d
echo BUILD_COMPLETE > C:\Users\aaron\Desktop\alphabetty\build_status.txt
"""
with sftp.open("C:/Users/aaron/Desktop/alphabetty/_rebuild.bat", "w") as f:
    f.write(build_script)
sftp.close()

# Remove old status
ssh.exec_command("del /f C:\\Users\\aaron\\Desktop\\alphabetty\\build_status.txt 2>nul")

# Create and run scheduled task
print("Creating scheduled task for build...")
stdin, stdout, stderr = ssh.exec_command(
    'schtasks /create /tn "Alphabetty-Rebuild" '
    '/tr "C:\\Users\\aaron\\Desktop\\alphabetty\\_rebuild.bat" '
    '/sc once /st 00:00 /f /ru "aaron" /rp "T0b1@n7243"',
    timeout=15,
)
out = stdout.read().decode()
err = stderr.read().decode()
print(out)
if err:
    print("ERR:", err)

print("Running build task...")
stdin, stdout, stderr = ssh.exec_command('schtasks /run /tn "Alphabetty-Rebuild"', timeout=15)
out = stdout.read().decode()
err = stderr.read().decode()
print(out)
if err:
    print("ERR:", err)

# Wait for completion
print("Waiting for build (polling every 5s)...")
for i in range(120):
    time.sleep(5)
    stdin, stdout, stderr = ssh.exec_command(
        "if exist C:\\Users\\aaron\\Desktop\\alphabetty\\build_status.txt "
        "(type C:\\Users\\aaron\\Desktop\\alphabetty\\build_status.txt) else (echo WAITING)",
        timeout=10,
    )
    result = stdout.read().decode().strip()
    if "BUILD_COMPLETE" in result:
        print(f"Build completed after {(i+1)*5}s")
        break
    elif "BUILD_FAILED" in result:
        print(f"Build FAILED after {(i+1)*5}s")
        break
    if i % 6 == 0:
        print(f"  Still building... ({(i+1)*5}s)")

# Check status
stdin, stdout, stderr = ssh.exec_command(
    "cd C:\\Users\\aaron\\Desktop\\alphabetty && docker compose ps", timeout=15
)
print("\nContainer status:")
print(stdout.read().decode())

stdin, stdout, stderr = ssh.exec_command(
    "cd C:\\Users\\aaron\\Desktop\\alphabetty && docker compose logs --tail=20", timeout=15
)
print("Recent logs:")
print(stdout.read().decode())

# Cleanup
ssh.exec_command('schtasks /delete /tn "Alphabetty-Rebuild" /f')
ssh.exec_command("del /f C:\\Users\\aaron\\Desktop\\alphabetty\\build_status.txt 2>nul")
ssh.exec_command("del /f C:\\Users\\aaron\\Desktop\\alphabetty\\_rebuild.bat 2>nul")
ssh.close()
print("Done!")
