import os
import cloudscraper
from bs4 import BeautifulSoup
import re
import json
import asyncio
from astrbot.api import logger

class NHCrawler:
    def __init__(self, proxy=None):
        # 使用更具体的浏览器指纹配置
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        self.base_url = "https://nhentai.net"

        # 配置代理: 优先使用传入的配置，否则读取环境变量
        if not proxy:
             proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")

        if proxy:
            self.scraper.proxies = {
                'http': proxy,
                'https': proxy
            }
            print(f"Crawler 使用代理: {proxy}")
        else:
            print("Crawler 未配置代理，将尝试直连。")

    async def get_popular_today(self, timeout=30):
        """获取今日热门列表

        Args:
            timeout: 请求超时时间（秒）

        Returns:
            List[Dict]: 本子列表，超时或出错返回空列表
        """
        # 严格使用用户指定的 URL: https://nhentai.net/language/chinese/popular-today
        target_url = f"{self.base_url}/language/chinese/popular-today"
        logger.debug(f"正在爬取: {target_url}")

        try:
            # 使用 asyncio.wait_for 包装同步请求，实现超时控制
            resp = await asyncio.wait_for(
                asyncio.to_thread(self.scraper.get, target_url, timeout=timeout),
                timeout=timeout + 5  # 额外5秒缓冲
            )

            if resp.status_code != 200:
                logger.warning(f"Failed to fetch page: {resp.status_code}")
                # 尝试备选方案：访问中文分类首页，通常前 5 个是热门
                target_url = f"{self.base_url}/language/chinese/"
                logger.debug(f"重试备选 URL: {target_url}")
                resp = await asyncio.wait_for(
                    asyncio.to_thread(self.scraper.get, target_url, timeout=timeout),
                    timeout=timeout + 5
                )
                if resp.status_code != 200:
                    return []

            soup = BeautifulSoup(resp.text, 'html.parser')

            # nhentai 的热门列表通常在 class="index-container index-popular" 中
            # 如果是 /popular-today 页面，可能所有的都在 index-container 中

            # 尝试查找明确标记为 "Popular" 的容器
            popular_container = soup.find('div', class_='index-popular')

            # 如果没有专门的 popular 容器（比如在 /popular-today 页面本身就是热门），则取第一个 index-container
            if not popular_container:
                popular_container = soup.find('div', class_='index-container')

            if not popular_container:
                logger.warning("未找到列表容器")
                return []

            results = []
            for gallery in popular_container.find_all('div', class_='gallery'):
                try:
                    link = gallery.find('a', class_='cover')
                    if not link: continue

                    href = link['href']
                    gid = re.search(r'/g/(\d+)/', href).group(1)
                    caption = gallery.find('div', class_='caption')
                    title = caption.text.strip() if caption else "Unknown Title"

                    # 尝试从 data-tags 获取 tags (如果有)
                    tags = link.get('data-tags', '').split(' ')

                    results.append({
                        'id': gid,
                        'title': title,
                        'url': f"{self.base_url}{href}",
                        'tags': tags
                    })
                except Exception as e:
                    logger.debug(f"解析单个画廊出错: {e}")
                    continue

            return results

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
            timeout=timeout + 5
        )

        if resp.status_code != 200:
            raise Exception(f"Failed to fetch gallery page: {resp.status_code}")

        # 尝试通过 regex 直接解析 window._gallery JSON 数据，这是最准确的方法
        # 格式通常是: window._gallery = JSON.parse("...");
        try:
            gallery_match = re.search(r'window\._gallery\s*=\s*JSON\.parse\((.*?)\);', resp.text, re.DOTALL)
            if gallery_match:
                raw_json_str = gallery_match.group(1)

                first_parse = json.loads(raw_json_str)
                if isinstance(first_parse, str):
                    gallery_data = json.loads(first_parse)
                else:
                    gallery_data = first_parse

                media_id = gallery_data.get('media_id')
                logger.debug(f"解析到 Media ID (JSON): {media_id}")

                images = gallery_data.get('images', {}).get('pages', [])

                if len(images) < min_pages:
                    logger.debug(f"本子页数过少 ({len(images)} < {min_pages})，跳过。")
                    return None  # 返回 None 表示被过滤，无需重试

                image_urls = []

                for i, img_data in enumerate(images, 1):
                    t = img_data.get('t')
                    ext = '.jpg'
                    if t == 'j': ext = '.jpg'
                    elif t == 'p': ext = '.png'
                    elif t == 'w': ext = '.webp'
                    elif t == 'g': ext = '.gif'

                    # 官方图片服务器: https://i.nhentai.net/galleries/{media_id}/{page}{ext}
                    real_url = f"https://i.nhentai.net/galleries/{media_id}/{i}{ext}"
                    image_urls.append(real_url)
                
                # 提取元数据 (放宽过滤条件)
                # 排除 language, category, translated，保留其他所有类型 (artist, group, parody, character, tag)
                tags = [t['name'] for t in gallery_data.get('tags', [])
                        if t.get('type') not in ('language', 'category', 'translated')]
                
                metadata = {
                    'title': gallery_data.get('title', {}).get('pretty') or gallery_data.get('title', {}).get('english'),
                    'tags': tags
                }

                if image_urls:
                    logger.debug(f"通过 JSON 解析构造了 {len(image_urls)} 个图片链接。")
                    return image_urls, metadata

        except Exception as e:
            logger.debug(f"JSON 解析失败，尝试 HTML 解析回退方案: {e}")

        # === 回退方案: HTML 解析 (旧逻辑) ===
        soup = BeautifulSoup(resp.text, 'html.parser')

        cover_img = soup.find('div', id='cover').find('img')
        if not cover_img:
            raise Exception("HTML parsing failed: Cover image not found")

        src = cover_img.get('data-src') or cover_img.get('src')
        match = re.search(r'/galleries/(\d+)/', src)
        if not match:
            raise Exception("HTML parsing failed: Media ID not found")

        media_id = match.group(1)
        logger.debug(f"解析到 Media ID (HTML): {media_id}")

        thumbs = soup.find_all('div', class_='thumb-container')

        if len(thumbs) < min_pages:
            logger.debug(f"本子页数过少 ({len(thumbs)} < {min_pages})，跳过 (HTML解析)。")
            return None

        image_urls = []
        for i, thumb in enumerate(thumbs, 1):
            img_tag = thumb.find('img')
            thumb_src = img_tag.get('data-src') or img_tag.get('src')

            ext = '.jpg'
            if '.webp' in thumb_src: ext = '.webp'
            elif '.png' in thumb_src: ext = '.png'
            elif '.gif' in thumb_src: ext = '.gif'

            real_url = f"https://i.nhentai.net/galleries/{media_id}/{i}{ext}"
            image_urls.append(real_url)
        
        # HTML 提取 tags
        tags_section = soup.find('section', id='tags')
        tags = []
        if tags_section:
            for tag_container in tags_section.find_all('span', class_='tags'):
                for t in tag_container.find_all('a', class_='tag'):
                     name_span = t.find('span', class_='name')
                     if name_span:
                         tags.append(name_span.text.strip())
        
        metadata = {'tags': tags}

        return image_urls, metadata
