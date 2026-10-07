import requests
import json
import base64
import os

url = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

def tts_http_stream():
    headers = {
        "X-Api-Key": "0c0a5457-b9c5-4e60-add7-5ad397260b9d", # API key可以从控制台获取
        "X-Api-Resource-Id": "seed-tts-2.0", 
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "X-Control-Require-Usage-Tokens-Return": "*" 
    }

    additions = {
        "disable_markdown_filter": False,
        "disable_emoji_filter": False,
        "enable_latex_tn": True,
        "context_texts": [
            "请说慢一些"
        ]
    }

    additions_json = json.dumps(additions)

    payload = {
        "req_params": {
            "text": "你好，语音测试2",
            "speaker": "zh_female_vv_uranus_bigtts",
            "additions": additions_json,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "enable_subtitle": True
            }
        }
    }
    session = requests.Session()
    response = None
    try:
        response = session.post(url, headers=headers, json=payload, stream=True)

        # used to save audio data
        audio_data = bytearray()
        total_audio_size = 0
        for chunk in response.iter_lines(decode_unicode=True):
            if not chunk:
                continue
            data = json.loads(chunk)
            print(f"json data:{data}")
            if data.get("code", 0) == 0 and "data" in data and data["data"]:
                chunk_audio = base64.b64decode(data["data"])
                audio_size = len(chunk_audio)
                total_audio_size += audio_size
                audio_data.extend(chunk_audio)
            if data.get("code", 0) == 20000000:
                break
            if data.get("code", 0) > 0:
                print(f"error response:{data}")
                break

        # save audio data to local file
        if audio_data:
            if not os.path.exists("tts"):
                os.makedirs("tts")
            output_file = os.path.join("tts/", f"tts_test.mp3")
            with open(output_file, "wb") as f:
                f.write(audio_data)
            print(f"file size: {len(audio_data) / 1024:.2f} KB")
            # ensure that the generated audio file has the correct access permissions
            os.chmod(output_file, 0o644)

    except Exception as e:
        print(f"request error: {e}")
    finally:
        if response:
            response.close()
        session.close()

if __name__ == "__main__":
    tts_http_stream()
