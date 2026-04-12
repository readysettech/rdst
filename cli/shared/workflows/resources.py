
def get_user_data(user_id: str, **_):
    return {
        "user_id": user_id,
        "name": "Alicia",
        "plan": "pro",
        "stats": {"queries": 124, "avg_latency_ms": 83}
    }

def summarize_info(**data):
    summary = f"{data.get('name')} ({data.get('user_id')}) on plan {data.get('plan')}, " \
              f"queries={data.get('stats', {}).get('queries')}"
    return {"summary": summary}

def compose_prompt(template: str, **kwargs):
    return {"prompt": template}