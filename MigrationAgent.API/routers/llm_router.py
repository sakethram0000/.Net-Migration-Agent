from fastapi import APIRouter
from agents.llm import check_connection, ask

router = APIRouter(prefix="/api/llm", tags=["llm"])

@router.get("/status")
def llm_status():
    connected = check_connection()
    return {
        "connected": connected,
        "model": "llama-3.3-70b-versatile (Groq)",
        "status": "ready" if connected else "not available"
    }

@router.get("/test")
def llm_test():
    try:
        response = ask("Say hello in one sentence.")
        return {"success": True, "response": response}
    except Exception as e:
        return {"success": False, "error": str(e)}
