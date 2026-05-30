from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_url: str = "https://api.z.ai/api/coding/paas/v4/chat/completions"
    llm_model: str = "glm-5.1"
    llm_api_key: str = ""
    ollama_url: str = "http://100.84.161.63:11434/v1/chat/completions"
    ollama_model: str = "qwen3:4b"

    # Search
    searxng_url: str = "http://100.84.161.63:8888"

    # Chrome CDP (undetected Chrome runs on :9222 inside container)
    cdp_url: str = "http://localhost:9222"
    cdp_vpn_url: str = "http://192.168.0.33:9222"
    chrome_profile: str = "/chrome-profile"
    chrome_window_size: str = "1920,1080"
    chrome_profiles_dir: str = "/chrome-profiles"  # Multi-profile parent dir

    # Smart Router (images)
    router_url: str = "http://100.84.161.63:4000"
    router_api_key: str = ""

    # App
    db_path: str = "/data/alphabetty.db"
    port: int = 7700
    upload_dir: str = "/uploads"
    download_dir: str = "/data/downloads"

    # Auth
    secret_key: str = "change-me-in-production"
    jwt_expire_hours: int = 72
    demo_enabled: bool = True
    demo_username: str = "demo"
    demo_password: str = "demo"
    bootstrap_token: str = "alphabetty-bootstrap-secret"

    # Agent bridge — comma-separated "name=url" pairs for outbound agent calling
    agent_endpoints: str = ""

    # Multi-model providers
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # n8n workflow automation
    n8n_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    # Media stack API (OpenAI-compatible tool endpoint)
    media_stack_url: str = "http://100.84.161.63:8070"

    # Swarm inter-instance routing
    swarm_name: str = ""        # This instance's identity (e.g. "oracle", "lappy")
    swarm_caps: str = ""        # Comma-separated capabilities (browser,gpu,llm_heavy,search,image_gen)
    swarm_peers: str = ""       # Comma-separated "name=url" pairs
    swarm_key: str = ""         # Shared auth key for inter-instance calls

    # Self-healing doctor
    doctor_enabled: bool = True
    doctor_timeout: int = 300       # Max seconds for doctor SSH session
    ssh_host: str = "100.84.161.63"  # Tailscale IP (primary)
    ssh_host_tailscale: str = "100.84.161.63"  # Tailscale fallback
    ssh_user: str = "aaron"
    ssh_pass: str = ""              # Set via ALPHABETTY_SSH_PASS
    ssh_port: int = 22

    model_config = {"env_prefix": "ALPHABETTY_"}


settings = Settings()
