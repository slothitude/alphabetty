from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_url: str = "https://api.z.ai/api/coding/paas/v4/chat/completions"
    llm_model: str = "glm-5.1"
    llm_api_key: str = ""
    ollama_url: str = "http://192.168.0.33:11434/v1/chat/completions"
    ollama_model: str = "qwen3:4b"

    # Search
    searxng_url: str = "http://192.168.0.33:8888"

    # Chrome CDP (undetected Chrome runs on :9222 inside container)
    cdp_url: str = "http://localhost:9222"
    cdp_vpn_url: str = "http://192.168.0.33:9222"
    chrome_profile: str = "/chrome-profile"
    chrome_window_size: str = "1920,1080"
    chrome_profiles_dir: str = "/chrome-profiles"  # Multi-profile parent dir

    # Smart Router (images)
    router_url: str = "http://192.168.0.33:4000"

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

    # n8n workflow automation
    n8n_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    model_config = {"env_prefix": "ALPHABETTY_"}


settings = Settings()
