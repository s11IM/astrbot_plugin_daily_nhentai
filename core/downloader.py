import os
import aiohttp
import asyncio
from astrbot.api import logger

class ImageDownloader:
    def __init__(self, max_concurrency=10, proxy=None):
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.proxy = proxy
        # 如果未传入，尝试从环境变量读取
        if not self.proxy:
            self.proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
            
        if self.proxy:
            logger.info(f"下载器使用代理: {self.proxy}")
        else:
            logger.debug("下载器未使用代理。如果下载失败，请检查网络连接或配置代理。")

    def _write_file(self, path, content):
        """同步写入文件（将在线程中运行）"""
        with open(path, 'wb') as f:
            f.write(content)

    async def download_image(self, session, url, save_path, retries=3):
        async with self.semaphore:
            for i in range(retries):
                try:
                    # 使用配置的代理进行下载
                    async with session.get(url, timeout=30, proxy=self.proxy) as response:
                        if response.status == 200:
                            content = await response.read()
                            # 使用 asyncio.to_thread 进行非阻塞文件写入
                            await asyncio.to_thread(self._write_file, save_path, content)
                            logger.debug(f"下载成功: {url}")
                            return True
                        elif response.status == 404:
                            logger.debug(f"下载失败 {url}: 404 Not Found (不再重试)")
                            return False
                        else:
                            logger.debug(f"下载失败 {url}: Status {response.status} (重试 {i+1}/{retries})")
                except Exception as e:
                    logger.debug(f"下载异常 {url}: {e} (重试 {i+1}/{retries})")
                
                # 如果不是最后一次尝试，稍微等待一下
                if i < retries - 1:
                    await asyncio.sleep(1)
            
            return False

    async def download_images(self, urls, output_dir):
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Referer": "https://nhentai.net/"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            tasks = []
            for url in urls:
                # 从 URL 中提取文件名 (例如 1.jpg)
                filename = url.split('/')[-1]
                save_path = os.path.join(output_dir, filename)
                task = asyncio.create_task(self.download_image(session, url, save_path))
                tasks.append(task)
            
            await asyncio.gather(*tasks)