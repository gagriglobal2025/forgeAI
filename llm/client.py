"""Thin wrapper around the Anthropic and OpenAI APIs used by every agent.

Kept deliberately small: one call in, one string out. Agents own prompt
construction and response parsing; this module owns only the network call,
retry/backoff, provider selection, and the --dry-run stub path.
"""
import os
import time

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.6",
}

API_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, model=None, provider="anthropic", max_tokens=8192, dry_run=False):
        if provider not in DEFAULT_MODELS:
            raise LLMError(f"Unknown provider {provider!r}. Supported: {list(DEFAULT_MODELS)}")

        self.provider = provider
        self.model = model or DEFAULT_MODELS[provider]
        self.max_tokens = max_tokens
        self.dry_run = dry_run
        self._client = None

        if not dry_run:
            env_var = API_KEY_ENV_VARS[provider]
            api_key = os.environ.get(env_var)
            if not api_key:
                raise LLMError(
                    f"{env_var} is not set. Export it before running, "
                    "or pass --dry-run to exercise the pipeline wiring without calling the API."
                )

            if provider == "anthropic":
                import anthropic  # imported lazily so --dry-run works without the package installed

                self._client = anthropic.Anthropic(api_key=api_key)
            else:
                import openai  # imported lazily so --dry-run works without the package installed

                self._client = openai.OpenAI(api_key=api_key)

    def complete(self, system_prompt: str, user_prompt: str, retries: int = 2) -> str:
        if self.dry_run:
            return self._stub_response(system_prompt, user_prompt)

        last_error = None
        for attempt in range(retries + 1):
            try:
                if self.provider == "anthropic":
                    text = self._complete_anthropic(system_prompt, user_prompt)
                else:
                    text = self._complete_openai(system_prompt, user_prompt)
                if not text or not text.strip():
                    raise LLMError(
                        "Model returned an empty response — likely spent its whole "
                        f"max_tokens budget ({self.max_tokens}) on internal reasoning with "
                        "none left for visible output. Retrying; if this persists, raise max_tokens."
                    )
                return text
            except Exception as exc:  # network/API errors, or the empty-response guard above: retry then surface
                last_error = exc
                if attempt < retries:
                    time.sleep(2 ** attempt)
        raise LLMError(f"LLM call failed after {retries + 1} attempts: {last_error}")

    def _complete_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def _complete_openai(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _stub_response(system_prompt: str, user_prompt: str) -> str:
        role_line = next(
            (line for line in system_prompt.splitlines() if line.startswith("ROLE:")),
            "ROLE: unknown_agent",
        )
        return (
            f"[DRY RUN STUB OUTPUT]\n{role_line}\n\n"
            "This is a placeholder artifact generated without calling the LLM.\n"
            "It exists only to prove the task DAG, artifact store, and approval "
            "gates wire together correctly.\n\nVERDICT: PASS\n"
        )
