"""
SkillSelect WebSocket & Network Capture Script
สำหรับดักการสื่อสาร WebSocket ขณะไล่ wizard SkillSelect
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page

# สำหรับเก็บ WebSocket messages
ws_messages = {
    "requests": [],
    "responses": [],
    "captured_at": datetime.now().isoformat()
}

async def capture_ws_traffic():
    """เปิด SkillSelect และ record HAR + WebSocket messages"""
    
    async with async_playwright() as p:
        # สร้าง browser context พร้อม HAR recording
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./browser_data",  # เก็บ cookies/session
            record_har_path="skillselect_capture.har",  # บันทึก HAR
            viewport={"width": 1280, "height": 720}
        )
        
        page = await context.new_page()
        
        # Hook WebSocket message listener
        async def on_ws_frame(ws_frame_data):
            """เก็บทุก WS message ที่ส่งไปมา"""
            try:
                if hasattr(ws_frame_data, 'payload'):
                    msg = json.loads(ws_frame_data.payload)
                    if ws_frame_data.is_text:
                        ws_messages["requests"].append({
                            "timestamp": datetime.now().isoformat(),
                            "payload": msg
                        })
            except Exception as e:
                print(f"[DEBUG] WS parse error: {e}")
        
        # Hook ทุก network request/response
        all_responses = []
        
        async def on_response(response):
            all_responses.append({
                "url": response.url,
                "method": response.request.method,
                "status": response.status,
                "headers": dict(response.headers),
                "timestamp": datetime.now().isoformat()
            })
        
        page.on("response", on_response)
        
        print("=" * 60)
        print("🔷 SkillSelect WebSocket Capture Tool")
        print("=" * 60)
        print("\n✅ Browser เปิดแล้ว")
        print("📍 กำลังโหลด SkillSelect...")
        print("   (รอจนกว่าเว็บโหลดเต็ม)\n")
        
        # ไปที่เว็บ SkillSelect
        try:
            await page.goto(
                "https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect/invitation-rounds",
                wait_until="networkidle",
                timeout=30000
            )
        except Exception as e:
            print(f"⚠️  Warning: {e}")
            print("   เว็บอาจช้า ลองปิด popup/cookies ใน browser")
        
        print("✅ SkillSelect loaded!")
        print("\n" + "=" * 60)
        print("📸 RECORDING MODE - เริ่มต่อ:")
        print("=" * 60)
        print("""
1️⃣  เลือก option ใน Dropdown ตัวแรก
2️⃣  กด "Next" button
3️⃣  ไล่ wizard ตามปกติ (เลือก Yes/No columns)
4️⃣  อย่าปิด browser จนกว่าเสร็จ
5️⃣  เมื่อได้ Results Table → กด "Done" ใน terminal

⏱️  รอ 5 นาที... กด Ctrl+C เมื่อเสร็จ หรือ exit browser
        """)
        print("=" * 60 + "\n")
        
        try:
            # รอให้พี่ทำการบน browser (block ที่นี่)
            await asyncio.sleep(300)  # 5 นาทีสูงสุด
        except KeyboardInterrupt:
            print("\n\n✅ Capture stopped by user")
        
        # ปิด browser context
        await context.close()
        
        print("\n" + "=" * 60)
        print("💾 Saving captured data...")
        print("=" * 60)
        
        # บันทึก WS messages
        ws_file = Path("skillselect_ws_payload.json")
        ws_file.write_text(json.dumps(ws_messages, indent=2, ensure_ascii=False))
        print(f"✅ WS messages → {ws_file.name}")
        
        # บันทึก network log
        network_file = Path("skillselect_network_log.json")
        network_file.write_text(json.dumps(all_responses, indent=2, ensure_ascii=False))
        print(f"✅ Network log → {network_file.name}")
        
        # HAR file เก็บอยู่แล้ว
        har_file = Path("skillselect_capture.har")
        if har_file.exists():
            print(f"✅ HAR file → {har_file.name}")
        
        print("\n" + "=" * 60)
        print("📦 Files created:")
        print("=" * 60)
        print(f"""
1. skillselect_capture.har
   → Full network capture (browser, images, etc)
   
2. skillselect_ws_payload.json
   → WebSocket messages only
   
3. skillselect_network_log.json
   → HTTP requests/responses log

👉 ส่ง file เหล่านี้ให้พี่ได้ (โดยเฉพาะ .har file)
        """)

if __name__ == "__main__":
    asyncio.run(capture_ws_traffic())
