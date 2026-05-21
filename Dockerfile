FROM python:3.13-slim

# Install Chrome + Xvfb + desktop deps for undetected Chrome
RUN apt-get update && apt-get install -y \
    wget gnupg2 curl \
    # Chrome deps
    fonts-liberation libasound2 libatk-bridge2.0-0 libdrm2 \
    libgtk-3-0 libnspr4 libnss3 libxss1 xdg-utils \
    libgbm1 libu2f-udev libvulkan1 \
    # Virtual display (headless Docker needs this for non-headless Chrome)
    xvfb \
    # Desktop environment basics
    dbus dbus-x11 at-spi2-core \
    # Useful fonts so pages look real
    fonts-noto-color-emoji fonts-dejavu fonts-liberation \
    # Process management
    procps psmisc \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome using modern GPG key method (apt-key is removed in Debian Trixie)
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Persistent Chrome user data directory (cookies, localStorage, etc.)
RUN mkdir -p /data /uploads /chrome-profile

EXPOSE 7700 9222

RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]
