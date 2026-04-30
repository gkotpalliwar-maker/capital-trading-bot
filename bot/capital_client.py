"""Capital.com REST API Client"""
import time
import requests
import logging
logger = logging.getLogger(__name__)

class CapitalClient:
    def __init__(self, api_url, api_key, email, password):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.email = email
        self.password = password
        self.security_token = None
        self.cst_token = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._token_time = None
        self._token_ttl = 540

    def _ensure_session(self):
        now = time.time()
        if self.security_token and self._token_time and (now - self._token_time) < self._token_ttl:
            return
        resp = self.session.post(f"{self.api_url}/api/v1/session",
            json={"identifier": self.email, "password": self.password},
            headers={"X-CAP-API-KEY": self.api_key}, timeout=15)
        resp.raise_for_status()
        self.security_token = resp.headers.get("X-SECURITY-TOKEN")
        self.cst_token = resp.headers.get("CST")
        self._token_time = now
        self.session.headers.update({"X-SECURITY-TOKEN": self.security_token, "CST": self.cst_token})
        logger.info("Session refreshed")

    def get(self, path, params=None):
        self._ensure_session()
        r = self.session.get(f"{self.api_url}{path}", params=params, timeout=15); r.raise_for_status(); return r.json()
    def post(self, path, data=None):
        self._ensure_session()
        r = self.session.post(f"{self.api_url}{path}", json=data, timeout=15); r.raise_for_status(); return r.json()
    def put(self, path, data=None):
        self._ensure_session()
        r = self.session.put(f"{self.api_url}{path}", json=data, timeout=15); r.raise_for_status(); return r.json()
    def delete(self, path, data=None):
        self._ensure_session()
        r = self.session.delete(f"{self.api_url}{path}", json=data, timeout=15); r.raise_for_status(); return r.json()
    def get_accounts(self): return self.get("/api/v1/accounts")
    def ping(self):
        try: self._ensure_session(); return True
        except Exception as e: logger.error(f"Connection failed: {e}"); return False
