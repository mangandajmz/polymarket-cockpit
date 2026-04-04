import random
import time
from dataclasses import dataclass

import requests


@dataclass
class JsonApiClient:
    default_timeout: float = 10.0
    default_retries: int = 3
    backoff_base: float = 1.0
    jitter_max: float = 0.25

    def get_json(self, url: str, params=None, timeout=None, retries=None):
        timeout = self.default_timeout if timeout is None else timeout
        retries = self.default_retries if retries is None else retries
        for attempt in range(retries):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception:
                if attempt < retries - 1:
                    delay = (self.backoff_base * (2 ** attempt)) + random.uniform(0, self.jitter_max)
                    time.sleep(delay)
        return None
