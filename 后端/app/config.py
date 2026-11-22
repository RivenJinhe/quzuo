"""配置：全部来自环境变量（前缀 QU_），不写死任何密钥。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="QU_", extra="ignore")

    # 数据库
    database_url: str = "postgresql+psycopg://qu:qu@127.0.0.1:55432/qu_dev"

    # 试用身份（BE-102）
    cookie_name: str = "qu_trial"
    cookie_secure: bool = False
    trial_ttl_days: int = 30
    trial_readonly_days: int = 30
    trial_purge_days: int = 37

    # 生成
    llm_provider: str = "stub"          # stub | openai_compatible
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: int = 60
    llm_allow_web_search: bool = False  # BE-402：必须为 False

    # 多模型（前端可见可切换，密钥只在服务端读，绝不下发）
    llm_allow_real: bool = False        # True 才允许真实模型出网
    llm_default_model: str = "stub/local"
    llm_max_compare: int = 4
    provider_deepseek_key: str = ""
    provider_kimi_key: str = ""
    provider_qwen_key: str = ""
    provider_minimax_key: str = ""

    # 限流与限额（BE-401 / BE-200）
    gen_rate_per_minute: int = 6
    max_materials_per_generation: int = 20
    max_excerpt_codepoints: int = 12000
    max_conditions_codepoints: int = 3000
    max_retained_codepoints: int = 2000
    max_pending_facts_codepoints: int = 1000
    poll_interval_seconds: int = 1

    # 链接抓取（BE-021~028）
    link_fetch_timeout_seconds: int = 5
    link_fetch_max_chars: int = 2000

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
