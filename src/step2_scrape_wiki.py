# src/step2_scrape_wiki.py
from src.config import WIKI_RAW_DIR
from src.seed_urls import SEED_URLS
from src.rag.wiki_scraper import scrape_urls

def main():
    out_jsonl = WIKI_RAW_DIR / "wiki_raw.jsonl"
    scrape_urls(SEED_URLS, out_jsonl=out_jsonl, sleep_s=0.8)
    print(f"\nSaved to: {out_jsonl}")

if __name__ == "__main__":
    main()
