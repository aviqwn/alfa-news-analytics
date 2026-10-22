"""Explicit live contract. No inferred model IDs or pricing."""
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

ALLOWED_MODELS = frozenset({"Gemma 4 26B A4B", "GLM-5.3-Flash", "Qwen3.6 35B A3B", "DeepSeek-V4.1-Flash", "Qwen3-VL-30B-A3B-Instruct", "Kimi-K2.6"})
NVIDIA_ENDPOINT = 'https://integrate.api.nvidia.com/v1/chat/completions'
NVIDIA_MODEL_ID = 'z-ai/glm-5.3-flash'
OPENROUTER_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODEL_ID = 'google/gemma-4-26b-a4b-it:free'
FREE_TRIAL_CONTRACTS = frozenset({
    (NVIDIA_ENDPOINT, NVIDIA_MODEL_ID, 'GLM-5.3-Flash'),
    (OPENROUTER_ENDPOINT, OPENROUTER_MODEL_ID, 'Gemma 4 26B A4B'),
})

@dataclass
class Config:
    mode: str = "test"
    endpoint: str = ""
    base_url: str = ""
    access_mode: str = "priced"
    json_mode: bool = False
    free_max_tokens_24h: int = 100000
    free_max_requests_24h: int = 20
    api_key: str = ""
    model_id: str = ""
    model_name: str = ""
    contract: str = ""
    input_price: str = ""
    output_price: str = ""
    mapping_confirmed: bool = False
    shared_ledger_confirmed: bool = False
    auth_token: str = ""
    company_scope: str = "*"
    data_mode: str = "auto"
    max_input_tokens: int = 24000
    max_output_tokens: int = 2400
    max_daily_requests: int = 100
    timeout: int = 30

    def __post_init__(self):
        if not self.endpoint and self.base_url:
            self.endpoint=self.base_url.rstrip('/')+'/chat/completions'

    @property
    def provider_name(self):
        if self.endpoint == OPENROUTER_ENDPOINT: return 'OpenRouter'
        if self.endpoint == NVIDIA_ENDPOINT: return 'NVIDIA NIM'
        return 'Настроенный API'

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv("APP_MODE", "test"), endpoint=os.getenv("LLM_ENDPOINT", ""),
            base_url=os.getenv('LLM_BASE_URL',''),access_mode=os.getenv('LLM_ACCESS_MODE','priced'),
            json_mode=os.getenv('LLM_JSON_MODE','false')=='true',
            free_max_tokens_24h=int(os.getenv('LLM_FREE_MAX_TOKENS_24H','100000')),
            free_max_requests_24h=int(os.getenv('LLM_FREE_MAX_REQUESTS_24H','20')),
            api_key=os.getenv("LLM_API_KEY", ""), model_id=os.getenv("LLM_MODEL_ID", ""),
            model_name=os.getenv("LLM_MODEL_NAME", ""), contract=os.getenv("LLM_API_CONTRACT", ""),
            input_price=os.getenv("LLM_INPUT_RUB_PER_MILLION", ""), output_price=os.getenv("LLM_OUTPUT_RUB_PER_MILLION", ""),
            mapping_confirmed=os.getenv("LLM_MAPPING_CONFIRMED") == "true",
            shared_ledger_confirmed=os.getenv("LLM_SHARED_LEDGER_CONFIRMED") == "true",
            auth_token=os.getenv("APP_TOKEN", ""), company_scope=os.getenv("APP_COMPANY_SCOPE", "*"), data_mode=os.getenv("APP_DATA_MODE", "auto"))

    def blockers(self):
        issues = []
        if self.mode not in {"test", "live"}: issues.append("APP_MODE должен быть test или live")
        if self.contract != "openai-chat-completions": issues.append("Не подтвержден API-контракт openai-chat-completions")
        p = urlsplit(self.endpoint)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
            issues.append("Не указан точный HTTPS endpoint провайдера")
        if not self.api_key: issues.append("Не задан серверный ключ LLM")
        if self.model_name not in ALLOWED_MODELS: issues.append("Не выбрана разрешенная модель из PDF")
        if not self.model_id or not self.mapping_confirmed: issues.append("API model ID и его соответствие списку не подтверждены")
        if self.access_mode=='free_trial':
            if (self.endpoint,self.model_id,self.model_name) not in FREE_TRIAL_CONTRACTS:
                issues.append('Бесплатный режим разрешен только для точных контрактов NVIDIA/GLM-5.3-Flash или OpenRouter/Gemma 4 26B A4B :free; платного fallback нет')
            if type(self.free_max_tokens_24h) is not int or not 1<=self.free_max_tokens_24h<=100000 or type(self.free_max_requests_24h) is not int or not 1<=self.free_max_requests_24h<=20:
                issues.append('Локальные пределы free trial: не более 100000 токенов и 20 запросов за 24 часа')
        elif self.access_mode=='priced':
            for name, value in [("вход", self.input_price), ("выход", self.output_price)]:
                try:
                    price = Decimal(value)
                    if not price.is_finite() or price <= 0: raise ValueError()
                except (InvalidOperation, ValueError): issues.append("Не подтвержден тариф RUB/1M: " + name)
        else:issues.append('LLM_ACCESS_MODE должен быть priced или free_trial')
        if self.access_mode=='priced' and not self.shared_ledger_confirmed:
            issues.append("Не подтвержден единый учет всех обращений общего ключа через этот ledger")
        return issues

    def max_cost(self):
        if self.access_mode=='free_trial':return None
        return float((Decimal(self.input_price) * self.max_input_tokens + Decimal(self.output_price) * self.max_output_tokens) / 1000000)
