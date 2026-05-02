from AIOralExamSystem.Tool.base_tool import BaseTool
import requests
import zipfile
import os
import re
from concurrent.futures import ThreadPoolExecutor
import time
import asyncio
from urllib.parse import urlparse
import glob

class FileParserTool(BaseTool):
    """文件解析工具"""
    def __init__(self, token: str, name: str):
        super().__init__(name)
        self.description = """解析文件内容"""
        self.mineru_api_url = "https://mineru.net/api/v4/file-urls/batch"
        self.header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

    async def _run(self, file_paths: list) -> str:
        """
        query: 用户查询的信息
        source: 用户的id，用于指定查询的文档来源
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(
                executor,
                self.getfile,
                file_paths
            )
        return chunks
    
    def getfile(self, file_paths: list):
        res = self.batch_upload(file_paths)
        batch_id = res["batch_id"]
        zip_paths = self.download_zip(batch_id, "./FILE/")

        file_chunks = []
        for zip_path in zip_paths:
            file_path = self.unzip_file(zip_path, "./newfile", os.path.basename(zip_path))
            md_files = glob.glob(os.path.join(file_path, "**", "full.md"), recursive=True)
            print(md_files)
            if not md_files:
                print(f"在 {file_path} 下未找到 full.md")
                continue
            with open(md_files[0], 'r', encoding='utf-8') as f:
                content = f.read()
                md_content = self.parse_md_to_chunks(content)
                file_chunks.append(md_content)
        
        return file_chunks

    def get_description(self) -> str:
        return self.description
    
    def upload_file(self, presigned_url, file_path):
        """异步上传单个文件到预签名URL"""
        try:
            # 异步读取文件内容
            with open(file_path, 'rb') as f:
                file_data = f.read()

            # 异步PUT上传
            res = requests.put(presigned_url, data=file_data)
            if res.status_code == 200:
                print(f"✅ {file_path} 上传成功")
                return True
            else:
                text = res.text
                print(f"上传失败2,{text}")
                return False
        except Exception as e:
            print(f"上传失败3")
            return False

    def batch_upload(self, file_paths):
        """异步批量上传主函数"""
        # 创建异步HTTP会话
        data = {
                "files": [
                    {"name": os.path.basename(file_path), "data_id": str(i+1)}
                    for i, file_path in enumerate(file_paths)
                ],
                "model_version":"vlm"
            }
        try:
            response = requests.post(self.mineru_api_url,headers=self.header,json=data)
            if response.status_code == 200:
                result = response.json()
                if result["code"] == 0:
                    batch_id = result["data"]["batch_id"]
                    urls = result["data"]["file_urls"]
                    print('batch_id:{},urls:{}'.format(batch_id, urls))
                    for i in range(0, len(urls)):
                        with open(file_paths[i], 'rb') as f:
                            res_upload = requests.put(urls[i], data=f)
                    return {
                        "batch_id": batch_id,
                        "urls": urls
                    }
                else:
                    print('apply upload url failed,reason:{}'.format(result["msg"]))
            else:
                print('response not success. status:{} ,result:{}'.format(response.status_code, response))
        except Exception as err:
            print(err)


    def download_zip(self, batch_id: str, SAVE_DIR: str):
        """
        同步下载ZIP文件到本地
        """
        ZIP_URL = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
        
        # 1. 准备保存目录和路径
        os.makedirs(SAVE_DIR, exist_ok=True)
        
        

        while True:
            response = requests.get(ZIP_URL, headers=self.header)
            result = response.json()
            all_done = True
            for status in result["data"]["extract_result"]:
                if status["state"] != "done":
                    all_done = False
                    break
            if all_done:
                break
            time.sleep(5)

        zip_paths = []
          # 如果状态码不是200，抛出异常
        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0

        
        # 3. 分块写入文件并显示进度
        for file in result["data"]["extract_result"]:
            response = requests.get(file["full_zip_url"], stream=True)
            response.raise_for_status()
            print(f"开始下载 {batch_id} 的结果...")
            filename = self.get_zip_filename_from_url(file["full_zip_url"])
            zip_path = os.path.join(SAVE_DIR, filename)
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1MB/块
                    if chunk:  # 过滤掉保持连接的新块
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            progress = downloaded / total * 100
                            print(f"\r下载进度: {progress:.1f}% ({downloaded}/{total} bytes)", end="")
            print(f"\n✅ 下载完成: {zip_path}")
            zip_paths.append(zip_path)
        return zip_paths
    
    def get_zip_filename_from_url(self, url: str):
        """
        从 URL 中提取 zip 文件名
        """
        if not url:
            return None
        
        # 1. 解析 URL，去掉 ? 和 # 后面的内容
        parsed_url = urlparse(url)
        path = parsed_url.path
        
        # 2. 获取路径的最后一部分
        filename = os.path.basename(path)
        
        # 3. (可选) 安全检查：确保它真的是个 zip 文件
        if filename.lower().endswith('.zip'):
            return filename
        else:
            return filename 



    def unzip_file(self, zip_path: str, SAVE_DIR: str, ZIP_FILENAME: str):
        """
        同步解压ZIP文件
        """
        extract_dir = os.path.join(SAVE_DIR, ZIP_FILENAME.replace(".zip", ""))
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # 计算总大小用于显示进度（可选）
                total_size = sum(file.file_size for file in zf.infolist())
                extracted_size = 0
                
                print(f"开始解压到 {extract_dir}...")
                for file in zf.infolist():  # 使用tqdm显示更美观的进度条<span 
                    zf.extract(file, extract_dir)
                    extracted_size += file.file_size

                
                print(f"\n✅ 解压完成: {extract_dir}")
                # 打印解压出来的文件列表
                for name in zf.namelist():
                    print(f"  📄 {name}")
                    
            return extract_dir
            
        except zipfile.BadZipFile:
            print(f"❌ 错误: {zip_path} 不是有效的ZIP文件或已损坏。")
            return None
        except Exception as e:
            print(f"❌ 解压时发生错误: {e}")
            return None
    
    def format_document(self, text: str) -> str:
        """保留整个文档结构，仅将里面的 <table> 替换为标准 MD 表格"""
        def convert_table(match):
            html_str = match.group(0)
            tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
            cell_pattern = re.compile(r'<(?:th|td)[^>]*>(.*?)</(?:th|td)>', re.DOTALL | re.IGNORECASE)
            
            md_lines = []
            is_first_row = True
            
            for row_match in tr_pattern.finditer(html_str):
                cells = [re.sub(r'<[^>]+>', '', cell.strip()) for cell in cell_pattern.findall(row_match.group(1))]
                if not cells: continue
                    
                md_lines.append("| " + " | ".join(cells) + " |")
                
                if is_first_row:
                    md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                    is_first_row = False
                    
            return "\n".join(md_lines)

        # 匹配全文所有 <table> 并调用内部函数替换
        return re.sub(r'<table.*?>.*?</table>', convert_table, text, flags=re.DOTALL | re.IGNORECASE)

    def parse_md_to_chunks(self, text: str) -> list:
        """
        1. 接收 str 类型的 md 文本
        2. 将所有 <table> 转变成 | --- | 格式
        3. 以任意 # 标题为边界切分，每个块必须附带其上方所有的层级标题路径
        """
        text = self.format_document(text)
        lines = [line.strip() for line in text.splitlines()]
        
        chunks = []
        
        # 【核心改动】使用栈来维护当前的层级路径
        # 栈的结构: [(level: int, title: str), ...]
        title_stack = []
        current_content_lines = [] 
        i = 0
        
        def save_chunk():
            
            nonlocal current_content_lines
            nonlocal title_stack
            chunk_str = "\n".join(current_content_lines).strip()
            if chunk_str:
                # 拼接完整的层级路径，例如: "# 总览 > ## 方法 > ### 算法"
                path_str = " > ".join([f"{'#' * lv} {t}" for lv, t in title_stack])
                chunks.append(f"{path_str}\n\n{chunk_str}")
            current_content_lines = []
            
        while i < len(lines):
            
            line = lines[i]
            
            # 匹配任意级别的标题 (#, ##, ###, #### 等)
            title_match = re.match(r'^(#{1,6})\s*(.*)', line)
            
            if title_match:
                save_chunk()  # 遇到新标题，前面的内容先存起来
                
                level = len(title_match.group(1))
                title_text = title_match.group(2).strip()
                
                # 【栈操作】维护层级关系
                # 1. 弹出栈中等于或低于当前层级的旧标题 (比如遇到##，就把栈里的##和###都弹出去)
                while title_stack and title_stack[-1][0] >= level:
                    title_stack.pop()
                # 2. 将当前新标题压入栈中
                title_stack.append((level, title_text))
                
                i += 1
            else:
                # 如果需要保留非表格文字，取消注释下一行：
                current_content_lines.append(line)
                i += 1
                
        save_chunk() # 处理文档末尾的残留内容
        return chunks