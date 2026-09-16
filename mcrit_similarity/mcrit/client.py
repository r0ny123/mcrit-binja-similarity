"""HTTP client for the MCRIT REST subset used by Binary Similarity.

Method names and routes match core ``mcrit.client.McritClient``. Requests are
one-shot (no shared Session) so overlapping provider visits stay thread-safe.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from mcrit_similarity.errors import McritAuthError, McritHttpError, McritUnavailableError

_SUCCESS = {"successful"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB
# MCRIT looks up each id separately, so keep single requests bounded.
_FUNCTION_BATCH = 500


def _normalize_server(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise McritUnavailableError("MCRIT server URL is empty")
    if not url.endswith("/"):
        url += "/"
    return url


class McritClient:
    def __init__(
        self,
        mcrit_server: str,
        apitoken: str | None = None,
        username: str | None = None,
        timeout: float | None = 30,
    ) -> None:
        self.mcrit_server = _normalize_server(mcrit_server)
        parsed = urlparse(self.mcrit_server)
        if apitoken and parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
            raise McritAuthError(
                f"Cannot transmit API token over unencrypted HTTP to non-loopback host '{parsed.hostname}'. "
                "Use HTTPS to protect credentials."
            )
        self.timeout = timeout if timeout and timeout > 0 else None
        self.headers: dict[str, str] = {}
        if apitoken:
            self.headers["apitoken"] = apitoken
        if username:
            self.headers["username"] = username

    def _url(self, path: str) -> str:
        return urljoin(self.mcrit_server, path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        try:
            response = requests.request(method, self._url(path), **kwargs)
        except requests.RequestException as exc:
            raise McritUnavailableError(f"MCRIT request failed: {exc}") from exc
        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> Any:
        status = response.status_code
        if status in (401, 403):
            raise McritAuthError(
                f"MCRIT rejected the request (HTTP {status}). "
                "Persisting samples through mcritweb requires a contributor or admin token."
            )
        if status in (301, 302, 303, 307, 308):
            raise McritHttpError(f"MCRIT server returned unexpected redirect HTTP {status}")
        if status in (404, 410):
            return None
        if status == 400:
            error_detail = "Bad Request"
            try:
                body = response.json()
                if isinstance(body, dict):
                    error_detail = body.get("message") or body.get("error") or str(body)
            except Exception:
                error_detail = getattr(response, "text", None) or error_detail
            raise McritHttpError(f"MCRIT rejected request (HTTP 400): {error_detail}")
        if status >= 500:
            raise McritHttpError(f"MCRIT server error HTTP {status}")
        if status not in (200, 202):
            raise McritHttpError(f"Unexpected MCRIT status HTTP {status}")

        content_length = getattr(response, "headers", {}).get("Content-Length")
        if content_length:
            try:
                if int(content_length) > _MAX_RESPONSE_BYTES:
                    raise McritHttpError(
                        f"MCRIT response exceeded maximum size limit ({content_length} bytes)"
                    )
            except ValueError:
                pass

        try:
            payload = response.json()
        except ValueError as exc:
            raise McritHttpError("MCRIT returned a non-JSON body") from exc
        if isinstance(payload, dict) and payload.get("status") in _SUCCESS:
            return payload.get("data")
        if isinstance(payload, dict) and "data" in payload:
            # 202 job envelopes sometimes omit status=successful
            return payload.get("data")
        return payload

    def get_version(self) -> str | None:
        data = self._request("GET", "version")
        if isinstance(data, dict):
            version = data.get("version")
            return str(version) if version is not None else None
        return None

    def get_sample_by_sha256(self, sample_sha256: str) -> dict[str, Any] | None:
        data = self._request("GET", f"samples/sha256/{sample_sha256}")
        return data if isinstance(data, dict) else None

    def add_report(self, smda_report: Any) -> dict[str, Any]:
        payload = smda_report.toDict() if hasattr(smda_report, "toDict") else smda_report
        data = self._request("POST", "samples", json=payload)
        if not isinstance(data, dict) or "sample_info" not in data:
            raise McritHttpError("MCRIT addReport did not return sample_info")
        return data

    def get_job_data(self, job_id: str) -> dict[str, Any] | None:
        data = self._request("GET", f"jobs/{job_id}")
        return data if isinstance(data, dict) else None

    def get_result(self, result_id: str, compact: bool = False) -> Any:
        query = "?compact=True" if compact else ""
        return self._request("GET", f"results/{result_id}{query}")

    def get_result_for_job(self, job_id: str, compact: bool = False) -> Any:
        query = "?compact=True" if compact else ""
        return self._request("GET", f"jobs/{job_id}/result{query}")

    def get_jobs(self, method: str, filter_text: str) -> list[dict[str, Any]]:
        """Jobs of ``method`` whose MCRIT parameter string (``method(arg, ...)``) contains the text."""
        # MCRIT applies ``filter`` after ``limit``, so no limit is sent.
        data = self._request("GET", "jobs", params={"method": method, "filter": filter_text})
        return [job for job in data if isinstance(job, dict)] if isinstance(data, list) else []

    def get_functions_by_ids(self, function_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Function entries without xcfg. The body is comma-separated ids, as MCRIT expects."""
        mapped: dict[int, dict[str, Any]] = {}
        for start in range(0, len(function_ids), _FUNCTION_BATCH):
            chunk = function_ids[start : start + _FUNCTION_BATCH]
            body = ",".join(str(int(function_id)) for function_id in chunk)
            data = self._request("POST", "functions", data=body)
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                try:
                    mapped[int(key)] = value
                except (TypeError, ValueError):
                    continue
        return mapped

    def request_matches_for_sample_vs(
        self,
        sample_id: int,
        other_sample_id: int,
        minhash_threshold: int | None = None,
        pichash_size: int | None = None,
        band_matches_required: int | None = None,
        force_recalculation: bool = False,
    ) -> Any:
        return self._request(
            "GET",
            f"matches/sample/{sample_id}/{other_sample_id}",
            params=_matching_params(
                minhash_threshold, pichash_size, band_matches_required, force_recalculation
            ),
        )

    def request_matches_for_sample(
        self,
        sample_id: int,
        minhash_threshold: int | None = None,
        pichash_size: int | None = None,
        band_matches_required: int | None = None,
        force_recalculation: bool = False,
    ) -> Any:
        """MatcherSample: one sample against the whole corpus, same result format as MatcherVs."""
        return self._request(
            "GET",
            f"matches/sample/{sample_id}",
            params=_matching_params(
                minhash_threshold, pichash_size, band_matches_required, force_recalculation
            ),
        )


def _matching_params(
    minhash_threshold: int | None,
    pichash_size: int | None,
    band_matches_required: int | None,
    force_recalculation: bool,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if minhash_threshold is not None:
        params["minhash_score"] = minhash_threshold
    if pichash_size is not None:
        params["pichash_size"] = pichash_size
    if band_matches_required is not None:
        params["band_matches_required"] = band_matches_required
    if force_recalculation:
        params["force_recalculation"] = True
    return params


# Kept for tests that want to inject a request callable.
RequestFn = Callable[..., Any]
