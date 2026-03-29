from pydantic import BaseModel, Field


class ExecRequest(BaseModel):
    command: str
    workdir: str = "/workspace"
    timeout: int = Field(default=30, ge=1, le=300)
    env: dict[str, str] = {}


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileReadRequest(BaseModel):
    path: str
