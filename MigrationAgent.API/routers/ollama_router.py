from fastapi import APIRouter
from agents.llm import check_connection, ask

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

@router.get("/status")
def ollama_status():
    connected = check_connection()
    return {
        "connected": connected,
        "model": "llama3-70b-8192 (Groq)",
        "status": "ready" if connected else "not available"
    }

@router.get("/test")
def ollama_test():
    try:
        response = ask("Say hello in one sentence.")
        return {"success": True, "response": response}
    except Exception as e:
        return {"success": False, "error": str(e)}
