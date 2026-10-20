"""ローカル画像生成サービス (SD1.5 / diffusers / CUDA)。
POST /generate {prompt, out_path} -> shared-data にPNG出力。"""
import os

import torch
from diffusers import StableDiffusionPipeline
from fastapi import FastAPI
from pydantic import BaseModel

SHARED = os.environ.get("SHARED_DIR", "/shared-data")
MODEL_ID = os.environ.get("SD_MODEL", "runwayml/stable-diffusion-v1-5")

app = FastAPI()
_pipe = None


def get_pipe():
    global _pipe
    if _pipe is None:
        _pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.float16,
            safety_checker=None, requires_safety_checker=False)
        if torch.cuda.is_available():
            _pipe = _pipe.to("cuda")
        _pipe.set_progress_bar_config(disable=True)
    return _pipe


class Gen(BaseModel):
    prompt: str
    out_path: str          # SHARED からの相対パス
    steps: int = 20
    width: int = 960
    height: int = 540


@app.get("/health")
def health():
    return {"ok": True, "cuda": torch.cuda.is_available()}


@app.post("/generate")
def generate(g: Gen):
    out = os.path.join(SHARED, g.out_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    style = ", flat illustration, dark blue background, no text"
    img = get_pipe()(g.prompt + style,
                     num_inference_steps=g.steps,
                     width=g.width, height=g.height).images[0]
    img.save(out)
    return {"file": g.out_path}
