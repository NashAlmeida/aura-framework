import time
import requests
import random

BASE_URL = "http://127.0.0.1:8000/agent/run"

PROMPTS = [
    "what's the weather in Mumbai",
    "open new tab and search griezmann",
    "list files on my desktop",
    "how much disk space do I have",
    "search for python tutorials",
    "open chrome and search for langfuse",
    "what is the weather in Delhi",
    "list files in downloads",
    "search for aura framework",
    "check disk usage",
    "create a simple hello world python file in cache",
    "create a notes.txt file in cache with a short project note",
    "make a basic python script in cache that prints the current time",
]


def send(prompt: str):
    try:
        r = requests.post(
            BASE_URL,
            json={"message": prompt},
            timeout=90,
        )
        print(f"[{r.status_code}] {prompt[:60]}... -> {r.text[:150]}", flush=True)
    except Exception as e:
        print(f"Error on '{prompt}': {e}", flush=True)


if __name__ == "__main__":
    print("Starting test loop (includes cache file creation). Press Ctrl+C to stop.", flush=True)
    while True:
        prompt = random.choice(PROMPTS)
        send(prompt)
        time.sleep(random.randint(30, 90))
