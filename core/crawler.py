import os
import cloudscraper
from bs4 import BeautifulSoup
import re
import json
import asyncio
from urllib.parse import urlparse
from astrbot.api import logger


class NHCrawler:
    def __init__(self, proxy=None):
        # 使用更具体的浏览器指纹配置
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        self.base_url = "https://nhentai.net"

        # 配置代理: 优先使用传入的配置，否则读取环境变量
        if not proxy:
            proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")

        if proxy:
            self.scraper.proxies = {"http": proxy, "https": proxy}
            print(f"Crawler 使用代理: {proxy}")
        else:
            print("Crawler 未配置代理，将尝试直连。")

    def _full_image_ext_from_thumb(self, thumb_src):
        """Extract the original page extension from a thumbnail URL."""
        filename = os.path.basename(urlparse(thumb_src).path)
        match = re.search(
            r"\d+t\.(jpg|jpeg|png|webp|gif)(?:\.[a-z0-9]+)?$", filename, re.IGNORECASE
        )
        if match:
            ext = match.group(1).lower()
            return ".jpg" if ext == "jpeg" else f".{ext}"

        lower_src = thumb_src.lower()
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            if ext in lower_src:
                return ".jpg" if ext == ".jpeg" else ext
        return ".jpg"

    def _extract_json_tags(self, gallery_data):
        """从 window._gallery 数据中提取可展示标签，过滤页数等统计项。"""
        excluded_types = {
            "language",
            "category",
            "translated",
            "page",
            "pages",
            "uploaded",
        }
        tags = []
        seen = set()

        for tag_data in gallery_data.get("tags", []):
            tag_type = str(tag_data.get("type", "")).strip().lower()
            if tag_type in excluded_types:
                continue

            name = str(tag_data.get("name", "")).strip()
            if not name:
                continue

            normalized = name.lower()
            if normalized in seen:
                continue

            seen.add(normalized)
            tags.append(name)

        return tags

    def _extract_html_tags(self, soup):
        """从详情页 HTML 中按分组提取可展示标签，避免把 Pages 当成标签。"""
        tags_section = soup.find("section", id="tags")
        if not tags_section:
            return []

        excluded_groups = {
            "languages",
            "language",
            "categories",
            "category",
            "pages",
            "page",
            "uploaded",
        }
        tags = []
        seen = set()

        for tag_container in tags_section.find_all("span", class_="tags"):
            group_name = ""
            previous_label = tag_container.find_previous(
                lambda tag: tag.name in ("div", "span")
                and "field-name" in tag.get("class", [])
            )
            if previous_label and tags_section in previous_label.parents:
                group_name = previous_label.get_text(" ", strip=True).rstrip(":").lower()

            if not group_name:
                group_text = tag_container.get_text(" ", strip=True).lower()
                group_name = group_text.split(":", 1)[0].strip() if group_text else ""

            if group_name in excluded_groups:
                continue

            for tag_link in tag_container.find_all("a", class_="tag"):
                name_span = tag_link.find("span", class_="name")
                if not name_span:
                    continue

                name = name_span.text.strip()
                if not name:
                    continue

                normalized = name.lower()
                if normalized in seen:
                    continue

                seen.add(normalized)
                tags.append(name)

        return tags

    def _get_non_popular_index_container(self, soup):
        """优先返回中文分类页的普通列表容器，而不是页面内的 Popular 区块。"""
        containers = soup.find_all("div", class_="index-container")
        for container in containers:
            classes = container.get("class", [])
            if "index-popular" not in classes:
                return container

        return containers[0] if containers else None

    def _extract_gallery_listing(self, container):
        """从列表容器中提取画廊基础信息。"""
        results = []
        for gallery in container.find_all("div", class_="gallery"):
            try:
                link = gallery.find("a", class_="cover")
                if not link:
                    continue

                href = link.get("href", "")
                gid_match = re.search(r"/g/(\d+)/", href)
                if not gid_match:
                    continue

                caption = gallery.find("div", class_="caption")
                title = caption.text.strip() if caption else "Unknown Title"

                # data-tags 是 nhentai 的数字标签 ID，不是可读标签名；详情页会再补充真实 tags。
                results.append(
                    {
                        "id": gid_match.group(1),
                        "title": title,
                        "url": f"{self.base_url}{href}",
                        "tags": [],
                    }
                )
            except Exception as e:
                logger.debug(f"解析单个画廊出错: {e}")
                continue

        return results

    async def get_popular_today(self, timeout=30):
        """获取中文分类页列表

        Args:
            timeout: 请求超时时间（秒）

        Returns:
            List[Dict]: 本子列表，超时或出错返回空列表
        """
        target_url = f"{self.base_url}/language/chinese/"
        logger.debug(f"正在爬取: {target_url}")

        try:
            # 使用 asyncio.wait_for 包装同步请求，实现超时控制
            resp = await asyncio.wait_for(
                asyncio.to_thread(self.scraper.get, target_url, timeout=timeout),
                timeout=timeout + 5,  # 额外5秒缓冲
            )

            if resp.status_code != 200:
                logger.warning(f"Failed to fetch page: {resp.status_code}")
                return []

            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            list_container = self._get_non_popular_index_container(soup)
            if not list_container:
                logger.warning("未找到列表容器")
                return []

            return self._extract_gallery_listing(list_container)

        except asyncio.TimeoutError:
            logger.warning(f"爬取列表超时（{timeout}秒）")
            return []
        except Exception as e:
            logger.error(f"爬取列表出错: {e}")
            return []

    async def get_gallery_images(self, gid, timeout=30, min_pages=35):
        """获取本子图片列表和信息

        Args:
            gid: 本子ID
            timeout: 请求超时时间（秒）
            min_pages: 最小页数限制，少于此页数将返回 None (默认 35)

        Returns:
            Tuple[List[str], Dict]: (图片URL列表, 元数据字典)
            None: 如果本子被过滤（如页数过少）

        Raises:
            Exception: 网络错误或其他异常，调用者应捕获并决定是否重试
        """
        url = f"{self.base_url}/g/{gid}/"

        # 移除外层 try-except，让异常抛出以便上层重试
        resp = await asyncio.wait_for(
            asyncio.to_thread(self.scraper.get, url, timeout=timeout),
            timeout=timeout + 5,
        )

        if resp.status_code != 200:
            raise Exception(f"Failed to fetch gallery page: {resp.status_code}")

        # 尝试通过 regex 直接解析 window._gallery JSON 数据，这是最准确的方法
        # 格式通常是: window._gallery = JSON.parse("...");
        try:
            gallery_match = re.search(
                r"window\._gallery\s*=\s*JSON\.parse\((.*?)\);", resp.text, re.DOTALL
            )
            if gallery_match:
                raw_json_str = gallery_match.group(1)

                first_parse = json.loads(raw_json_str)
                if isinstance(first_parse, str):
                    gallery_data = json.loads(first_parse)
                else:
                    gallery_data = first_parse

                media_id = gallery_data.get("media_id")
                logger.debug(f"解析到 Media ID (JSON): {media_id}")

                images = gallery_data.get("images", {}).get("pages", [])

                if len(images) < min_pages:
                    logger.debug(f"本子页数过少 ({len(images)} < {min_pages})，跳过。")
                    return None  # 返回 None 表示被过滤，无需重试

                image_urls = []

                for i, img_data in enumerate(images, 1):
                    t = img_data.get("t")
                    ext = ".jpg"
                    if t == "j":
                        ext = ".jpg"
                    elif t == "p":
                        ext = ".png"
                    elif t == "w":
                        ext = ".webp"
                    elif t == "g":
                        ext = ".gif"

                    # 官方图片服务器: https://i.nhentai.net/galleries/{media_id}/{page}{ext}
                    real_url = f"https://i.nhentai.net/galleries/{media_id}/{i}{ext}"
                    image_urls.append(real_url)

                # 提取元数据：只保留可读且适合展示的标签类型，排除 language/category/pages 等统计项。
                tags = self._extract_json_tags(gallery_data)

                metadata = {
                    "title": gallery_data.get("title", {}).get("pretty")
                    or gallery_data.get("title", {}).get("english"),
                    "tags": tags,
                }

                if image_urls:
                    logger.debug(f"通过 JSON 解析构造了 {len(image_urls)} 个图片链接。")
                    return image_urls, metadata

        except Exception as e:
            logger.debug(f"JSON 解析失败，尝试 HTML 解析回退方案: {e}")

        # === 回退方案: HTML 解析 (旧逻辑) ===
        soup = BeautifulSoup(resp.text, "html.parser")

        cover_img = soup.find("div", id="cover").find("img")
        if not cover_img:
            raise Exception("HTML parsing failed: Cover image not found")

        src = cover_img.get("data-src") or cover_img.get("src")
        match = re.search(r"/galleries/(\d+)/", src)
        if not match:
            raise Exception("HTML parsing failed: Media ID not found")

        media_id = match.group(1)
        logger.debug(f"解析到 Media ID (HTML): {media_id}")

        thumbs = soup.find_all("div", class_="thumb-container")

        if len(thumbs) < min_pages:
            logger.debug(
                f"本子页数过少 ({len(thumbs)} < {min_pages})，跳过 (HTML解析)。"
            )
            return None

        image_urls = []
        for i, thumb in enumerate(thumbs, 1):
            img_tag = thumb.find("img")
            thumb_src = img_tag.get("data-src") or img_tag.get("src")

            ext = self._full_image_ext_from_thumb(thumb_src)

            real_url = f"https://i.nhentai.net/galleries/{media_id}/{i}{ext}"
            image_urls.append(real_url)

        # HTML 提取 tags。nhentai 会把 Pages/Uploaded 也做成 tag 样式，必须按分组过滤。
        tags = self._extract_html_tags(soup)

        metadata = {"tags": tags}

        return image_urls, metadata
