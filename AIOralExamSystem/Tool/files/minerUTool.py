import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


class MinerUFileTool:
    def __init__(self, token: str, api_url: str | None = None):
        self.mineru_api_url = api_url or "https://mineru.net/api/v4/file-urls/batch"
        self.header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def parse_to_markdown_files(self, file_paths: list[str], work_root: Path) -> list[Path]:
        work_root = Path(work_root)
        cache_root = work_root / "mineru_cache"
        pending_specs = []
        markdown_by_source = {}

        for file_path in file_paths:
            path = Path(file_path)
            file_hash = self.get_file_sha256(path)
            cached_markdown = self.get_cached_markdown(cache_root, file_hash)
            if cached_markdown:
                markdown_by_source[str(path)] = cached_markdown
                continue
            pending_specs.append({
                "path": path,
                "hash": file_hash,
            })

        if pending_specs:
            parsed_markdowns = self.parse_pending_files_with_mineru(pending_specs, work_root)
            markdown_by_source.update(parsed_markdowns)

        return [
            markdown_by_source[str(Path(file_path))]
            for file_path in file_paths
            if str(Path(file_path)) in markdown_by_source
        ]

    def parse_pending_files_with_mineru(
        self,
        pending_specs: list[dict],
        work_root: Path,
    ) -> dict[str, Path]:
        zip_dir = work_root / "zips"
        extract_dir = work_root / "extracted"
        file_paths = [str(spec["path"]) for spec in pending_specs]

        res = self.batch_upload(file_paths)
        if not res:
            raise RuntimeError("MinerU batch upload failed; no batch result returned.")

        batch_id = res["batch_id"]
        zip_paths = self.download_zip(batch_id, str(zip_dir))
        markdown_by_source = {}

        for spec, zip_path in zip(pending_specs, zip_paths):
            extracted_path = self.unzip_file(
                zip_path,
                str(extract_dir),
                os.path.basename(zip_path),
            )
            if not extracted_path:
                continue

            markdown_path = self.find_full_markdown(Path(extracted_path))
            if not markdown_path:
                print(f"full.md not found under {extracted_path}")
                continue

            cached_markdown = self.save_markdown_cache(
                source_path=spec["path"],
                source_hash=spec["hash"],
                markdown_path=markdown_path,
                cache_root=work_root / "mineru_cache",
            )
            markdown_by_source[str(spec["path"])] = cached_markdown

        return markdown_by_source

    def get_file_sha256(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def get_cached_markdown(self, cache_root: Path, source_hash: str) -> Path | None:
        markdown_path = cache_root / source_hash / "full.md"
        if markdown_path.is_file() and markdown_path.stat().st_size > 0:
            return markdown_path
        return None

    def save_markdown_cache(
        self,
        source_path: Path,
        source_hash: str,
        markdown_path: Path,
        cache_root: Path,
    ) -> Path:
        cache_dir = cache_root / source_hash
        cache_dir.mkdir(parents=True, exist_ok=True)

        cached_markdown = cache_dir / "full.md"
        shutil.copyfile(markdown_path, cached_markdown)

        stat = source_path.stat()
        metadata = {
            "source_path": str(source_path.resolve()),
            "source_name": source_path.name,
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "source_sha256": source_hash,
            "parser": "mineru",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (cache_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return cached_markdown

    def find_full_markdown(self, extracted_path: Path) -> Path | None:
        md_files = sorted(extracted_path.glob("**/full.md"))
        if md_files:
            print([str(path) for path in md_files])
            return md_files[0]
        return None

    def upload_file(self, presigned_url, file_path):
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            res = requests.put(presigned_url, data=file_data)
            if res.status_code == 200:
                print(f"{file_path} upload success")
                return True
            print(f"upload failed, {res.text}")
            return False
        except Exception:
            print("upload failed")
            return False

    def batch_upload(self, file_paths):
        data = {
            "files": [
                {"name": os.path.basename(file_path), "data_id": str(i + 1)}
                for i, file_path in enumerate(file_paths)
            ],
            "model_version": "vlm",
        }
        try:
            response = requests.post(self.mineru_api_url, headers=self.header, json=data)
            if response.status_code == 200:
                result = response.json()
                if result["code"] == 0:
                    batch_id = result["data"]["batch_id"]
                    urls = result["data"]["file_urls"]
                    print("batch_id:{},urls:{}".format(batch_id, urls))
                    for i in range(0, len(urls)):
                        with open(file_paths[i], "rb") as f:
                            requests.put(urls[i], data=f)
                    return {
                        "batch_id": batch_id,
                        "urls": urls,
                    }
                print("apply upload url failed,reason:{}".format(result["msg"]))
            else:
                print("response not success. status:{} ,result:{}".format(response.status_code, response))
        except Exception as err:
            print(err)
        return None

    def download_zip(self, batch_id: str, save_dir: str):
        zip_url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
        os.makedirs(save_dir, exist_ok=True)

        while True:
            response = requests.get(zip_url, headers=self.header)
            result = response.json()
            all_done = True
            for status in result["data"]["extract_result"]:
                print(status)
                if status["state"] != "done":
                    all_done = False
                    break
            if all_done:
                break
            time.sleep(5)

        zip_paths = []
        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0

        for file in result["data"]["extract_result"]:
            response = requests.get(file["full_zip_url"], stream=True)
            response.raise_for_status()
            print(f"start downloading result for {batch_id}...")
            filename = self.get_zip_filename_from_url(file["full_zip_url"])
            zip_path = os.path.join(save_dir, filename)
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            progress = downloaded / total * 100
                            print(f"\rdownload progress: {progress:.1f}% ({downloaded}/{total} bytes)", end="")
            print(f"\ndownload complete: {zip_path}")
            zip_paths.append(zip_path)
        return zip_paths

    def get_zip_filename_from_url(self, url: str):
        if not url:
            return None

        parsed_url = urlparse(url)
        path = parsed_url.path
        return os.path.basename(path)

    def unzip_file(self, zip_path: str, save_dir: str, zip_filename: str):
        extract_dir = os.path.join(save_dir, zip_filename.replace(".zip", ""))
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                print(f"start extracting to {extract_dir}...")
                for file in zf.infolist():
                    zf.extract(file, extract_dir)

                print(f"\nextract complete: {extract_dir}")
                for name in zf.namelist():
                    print(f"  {name}")

            return extract_dir

        except zipfile.BadZipFile:
            print(f"error: {zip_path} is not a valid zip file or is corrupted.")
            return None
        except Exception as e:
            print(f"error while extracting zip: {e}")
            return None
