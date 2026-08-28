import time

import requests

import config


class HevyAPIError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Hevy API error {status_code}: {message}")


class HevyClient:
    def __init__(self, api_key, base_url=None, timeout=None):
        self.base_url = base_url or config.API_BASE_URL
        self.timeout = timeout or config.REQUEST_TIMEOUT_SECONDS
        self.session = requests.Session()
        self.session.headers.update({"api-key": api_key, "Accept": "application/json"})

    def _request(self, method, path, params=None, json_body=None, retries=2):
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body, timeout=self.timeout
                )
            except requests.RequestException as exc:
                attempt += 1
                if attempt > retries:
                    raise HevyAPIError(0, f"network error: {exc}") from exc
                time.sleep(attempt)
                continue

            if resp.status_code in (429, 503) and attempt < retries:
                attempt += 1
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else attempt * config.RATE_LIMIT_BACKOFF_SECONDS
                time.sleep(delay)
                continue

            if resp.status_code >= 500 and attempt < retries:
                attempt += 1
                time.sleep(attempt)
                continue

            if resp.status_code >= 400:
                try:
                    message = resp.json().get("error", resp.text)
                except ValueError:
                    message = resp.text
                raise HevyAPIError(resp.status_code, message or resp.reason)

            if not resp.content:
                return {}
            return resp.json()

    def get_workout_count(self):
        data = self._request("GET", "/v1/workouts/count")
        return data.get("workout_count")

    def get_user_info(self):
        return self._request("GET", "/v1/user/info")

    def list_workouts_page(self, page, page_size):
        return self._request("GET", "/v1/workouts", params={"page": page, "pageSize": page_size})

    def list_workout_events_page(self, since, page, page_size):
        return self._request(
            "GET",
            "/v1/workouts/events",
            params={"since": since, "page": page, "pageSize": page_size},
        )

    def list_exercise_templates_page(self, page, page_size):
        return self._request(
            "GET", "/v1/exercise_templates", params={"page": page, "pageSize": page_size}
        )

    def list_body_measurements_page(self, page, page_size):
        return self._request(
            "GET", "/v1/body_measurements", params={"page": page, "pageSize": page_size}
        )

    def list_routines_page(self, page, page_size):
        return self._request("GET", "/v1/routines", params={"page": page, "pageSize": page_size})

    def update_routine(self, routine_id, routine):
        """Replaces a routine's contents in place. The id is preserved, so workouts
        already logged against it stay linked."""
        return self._request("PUT", f"/v1/routines/{routine_id}", json_body={"routine": routine})

    def paginate(self, fetch_page_fn, items_key, page_size, start_page=1):
        """Yields (page, page_count, items) for each page, sleeping politely between calls."""
        page = start_page
        while True:
            data = fetch_page_fn(page=page, page_size=page_size)
            items = data.get(items_key, [])
            page_count = data.get("page_count", page)
            yield page, page_count, items
            if page >= page_count:
                break
            page += 1
            time.sleep(config.REQUEST_DELAY_SECONDS)
