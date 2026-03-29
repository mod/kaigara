"""HTTP clients for inter-container communication."""

import httpx


class ToolsClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def tool(self, name: str, args: dict) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/tool/{name}", json=args, timeout=60
            )
            resp.raise_for_status()
            return resp.json()

    async def list_tools(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/tools", timeout=10)
            resp.raise_for_status()
            return resp.json()


class SandboxClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def exec(
        self, command: str, workdir: str = "/workspace", timeout: int = 30
    ) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/exec",
                json={"command": command, "workdir": workdir, "timeout": timeout},
                timeout=timeout + 5,
            )
            resp.raise_for_status()
            return resp.json()
