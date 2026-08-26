from fastapi import FastAPI
from datetime import datetime
import uvicorn

app = FastAPI()

# 오늘 하루치 점검 기록을 메모리에 임시 저장 (서버 재시작하면 초기화됨)
today_inspections = []

@app.get("/api/equipment/checklist")
def get_checklist():
    return [
        {"equipment_id": "pump_01", "edge_type": "수계", "status": "warning"},
        {"equipment_id": "detector1_zone_a", "edge_type": "자탐1", "status": "normal"},
        {"equipment_id": "detector2_zone_b", "edge_type": "자탐2", "status": "normal"},
        {"equipment_id": "gas_tank_01", "edge_type": "가스계", "status": "normal"},
        {"equipment_id": "extinguisher_04", "edge_type": "소화기", "status": "normal"},
        {"equipment_id": "evac_light_03", "edge_type": "유도등", "status": "alarm"},
    ]

@app.post("/api/equipment/{id}/inspection")
def post_inspection(id: str, body: dict):
    record = {
        "equipment_id": id,
        "inspector": body.get("inspector", "미상"),
        "status": body.get("status", "normal"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    today_inspections.append(record)
    print(f"점검완료 기록: {record}")
    return {"result": "saved"}

@app.get("/api/inspections/today")
def get_today_inspections():
    return today_inspections

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
