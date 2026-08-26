from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()

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
    print(f"점검완료 기록: {id} / {body}")
    return {"result": "saved"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)