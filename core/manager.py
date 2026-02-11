import os
import shutil
import asyncio
import threading
from astrbot.api import logger
from .crawler import NHCrawler
from .downloader import ImageDownloader
from .analyzer import NSFWAnalyzer
from .renderer import ResultRenderer

class DailyManager:
    def __init__(self, context, config):
        self.context = context
        self.config = config
        self._lock = asyncio.Lock()  # 并发锁
        
        # 从配置中获取参数
        proxy = config.get("proxy_url", "")
        threshold = float(config.get("model_threshold", 0.08)) # Default to 0.08 as per docs
        device = config.get("model_device", "")
        self.min_pages = int(config.get("min_pages", 35))

        self.crawler = NHCrawler(proxy=proxy)
        self.downloader = ImageDownloader(proxy=proxy)

        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        self.analyzer = NSFWAnalyzer(models_dir, threshold=threshold, device=device)
        self.renderer = ResultRenderer()
        
        # 启动时清理缓存 (简单清理)
        self._cleanup_cache()

    def _cleanup_cache(self):
        try:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            cache_dir = os.path.join(base_dir, "cache")
            if os.path.exists(cache_dir):
                logger.info("正在清理缓存目录...")
                for item in os.listdir(cache_dir):
                    item_path = os.path.join(cache_dir, item)
                    try:
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.unlink(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                    except Exception as e:
                        logger.warning(f"删除 {item_path} 失败: {e}")
            else:
                os.makedirs(cache_dir)
        except Exception as e:
            logger.warning(f"缓存清理失败: {e}")

    async def process_daily_ranking(self, total_timeout=1200, analyze_timeout=300):
        """处理每日热门排行榜

        Args:
            total_timeout: 整体流程超时时间（秒），默认20分钟
            analyze_timeout: 单个本子分析超时时间（秒），默认5分钟

        Returns:
            str: 生成的图片路径，失败返回None

        Raises:
            asyncio.TimeoutError: 整体流程超时
            Exception: 其他错误
        """
        if self._lock.locked():
            raise Exception("已有任务正在进行中，请稍后再试")

        async with self._lock:
            try:
                # 使用整体超时控制
                return await asyncio.wait_for(
                    self._process_daily_ranking_internal(analyze_timeout),
                    timeout=total_timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"整体流程超时（{total_timeout}秒），强制中断")
                raise asyncio.TimeoutError(f"处理超时（{total_timeout}秒），请稍后重试")
            except Exception as e:
                logger.error(f"处理过程发生错误: {e}")
                raise

    async def _process_daily_ranking_internal(self, analyze_timeout):
        """内部处理函数"""
        # 1. 获取今日中文热门列表
        logger.info("开始获取今日中文热门列表...")
        galleries = await self.crawler.get_popular_today(timeout=30)
        if not galleries:
            logger.warning("未能获取到热门列表。")
            return None

        # 使用插件目录下的 cache 文件夹
        base_dir = os.path.dirname(os.path.dirname(__file__))
        cache_dir = os.path.join(base_dir, "cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        logger.info(f"获取到 {len(galleries)} 个本子，开始并行处理...")

        # 队列
        download_queue = asyncio.Queue()
        analyze_queue = asyncio.Queue()
        analyzed_galleries = []
        failed_galleries = []  # 记录失败的本子
        skipped_galleries = [] # 记录跳过的本子
        
        # 将任务放入下载队列
        for gallery in galleries:
            await download_queue.put(gallery)

        # Worker 函数：下载
        async def download_worker():
            while True:
                try:
                    gallery = download_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                gid = gallery['id']
                logger.debug(f"[下载] 开始处理 ID: {gid}")

                gallery_dir = os.path.join(cache_dir, str(gid))
                try:
                    # 获取图片链接（带超时，增加重试机制）
                    image_urls = []
                    metadata = {}
                    
                    is_filtered = False
                    
                    for retry in range(3): # 尝试 3 次
                        try:
                            result = await self.crawler.get_gallery_images(gid, timeout=30, min_pages=self.min_pages)
                            if result is None:
                                logger.debug(f"[下载] {gid} 被过滤（如页数不足 {self.min_pages}），跳过")
                                is_filtered = True
                                break
                                
                            image_urls, metadata = result
                            if image_urls:
                                break
                        except Exception as e:
                            logger.debug(f"[下载] {gid} 获取链接出错: {e}，重试 {retry+1}/3...")
                            if retry < 2:
                                await asyncio.sleep(2)

                    if is_filtered:
                        skipped_galleries.append(gid)
                        download_queue.task_done()
                        continue

                    if not image_urls:
                        logger.warning(f"[下载] 无法获取 {gid} 的图片链接 (已重试3次)")
                        failed_galleries.append(gid)
                        download_queue.task_done()
                        continue
                    
                    # 更新元数据（如 tags）
                    if metadata:
                        gallery.update(metadata)

                    # 下载图片
                    await self.downloader.download_images(image_urls, gallery_dir)

                    # 放入分析队列
                    gallery['gallery_dir'] = gallery_dir
                    await analyze_queue.put(gallery)
                    logger.info(f"[下载完成] {gid} ({len(image_urls)}页) - 已加入分析队列")

                except asyncio.TimeoutError:
                    logger.warning(f"[下载] 处理 {gid} 超时")
                    failed_galleries.append(gid)
                except Exception as e:
                    logger.error(f"[下载] 处理 {gid} 出错: {e}")
                    failed_galleries.append(gid)
                finally:
                    download_queue.task_done()

        # Worker 函数：分析
        async def analyze_worker():
            while True:
                try:
                    gallery = await analyze_queue.get()
                except asyncio.CancelledError:
                    break

                gid = gallery['id']
                gallery_dir = gallery.get('gallery_dir')
                logger.debug(f"[分析] 正在分析: {gid} - {gallery.get('title', 'Unknown')[:30]}...")

                stop_event = threading.Event()
                try:
                    # 分析（带超时控制，传入stop_event）
                    score, nsfw_stats = await asyncio.wait_for(
                        asyncio.to_thread(self.analyzer.analyze_folder, gallery_dir, stop_event),
                        timeout=analyze_timeout
                    )

                    gallery['score'] = score
                    gallery['stats'] = nsfw_stats

                    # 提取封面 (支持多种格式)
                    cover_found = False
                    for ext in ['jpg', 'png', 'webp']:
                        cover_path = os.path.join(gallery_dir, f"1.{ext}")
                        if os.path.exists(cover_path):
                             saved_cover = os.path.join(cache_dir, f"cover_{gid}.{ext}")
                             
                             # 确保目标文件不存在，防止 move 失败
                             if os.path.exists(saved_cover):
                                 try:
                                     os.remove(saved_cover)
                                 except Exception as e:
                                     logger.warning(f"删除旧封面失败: {e}")

                             try:
                                 shutil.move(cover_path, saved_cover)
                                 gallery['local_cover'] = saved_cover
                                 cover_found = True
                             except Exception as e:
                                 logger.error(f"移动封面失败 {gid}: {e}")
                             
                             break
                    
                    if not cover_found:
                        logger.warning(f"[分析] {gid} 未找到封面图片")

                    analyzed_galleries.append(gallery)
                    # 提升为 INFO 级别，方便用户了解进度
                    title_snippet = gallery.get('title', 'Unknown')[:20]
                    logger.info(f"[分析完成] ID:{gid} 得分:{score:.2f} 标题:{title_snippet}...")

                except asyncio.TimeoutError:
                    logger.warning(f"[分析] 分析 {gid} 超时（{analyze_timeout}秒）")
                    stop_event.set() # 触发停止信号
                    failed_galleries.append(gid)
                except Exception as e:
                    logger.error(f"[分析] 出错 {gid}: {e}")
                    failed_galleries.append(gid)
                finally:
                    # 清理
                    if gallery_dir and os.path.exists(gallery_dir):
                        try:
                            shutil.rmtree(gallery_dir)
                        except Exception as e:
                            logger.warning(f"清理临时目录失败 {gallery_dir}: {e}")
                    analyze_queue.task_done()

        # 启动 Workers
        # 下载 Worker 数量可以多一点 (IO密集)
        download_workers = [asyncio.create_task(download_worker()) for _ in range(3)]

        # 分析 Worker 数量少一点 (CPU/GPU密集)，甚至1个，避免显存爆炸
        analyze_workers = [asyncio.create_task(analyze_worker()) for _ in range(1)]

        # 等待所有下载完成
        await download_queue.join()

        # 此时所有任务都已进入分析队列（或被丢弃），等待分析完成
        await analyze_queue.join()

        # 取消分析 Worker (因为它们在 while True 中等待)
        for w in analyze_workers:
            w.cancel()

        # 确保下载 Worker 也结束（其实它们因为 queue empty 应该已经退出了）
        await asyncio.gather(*download_workers, return_exceptions=True)

        # 检查结果
        logger.info(f"处理完成: 成功 {len(analyzed_galleries)} 个, 跳过 {len(skipped_galleries)} 个, 失败 {len(failed_galleries)} 个")

        if not analyzed_galleries:
            logger.warning("没有成功分析任何本子")
            return None

        # 排序与生成结果
        logger.info("生成结果卡片...")
        analyzed_galleries.sort(key=lambda x: x.get('score', 0), reverse=True)
        top_n = analyzed_galleries[:10] # 保留前10个

        # 检查是否有足够的本子生成卡片
        if len(top_n) == 0:
            logger.warning("没有足够的本子生成结果卡片")
            return None

        output_path = os.path.join(cache_dir, "nh_daily_result.jpg")
        final_card = self.renderer.render_card(top_n, output_path)

        # 清理封面图
        for g in analyzed_galleries:  # 清理所有封面临时文件
            if 'local_cover' in g and os.path.exists(g['local_cover']):
                try:
                    os.remove(g['local_cover'])
                except Exception as e:
                    logger.debug(f"清理封面失败 {g['local_cover']}: {e}")

        return final_card

    async def process_single_gallery(self, gid):
        """处理单个本子"""
        logger.info(f"开始处理单个本子: {gid}")
        
        base_dir = os.path.dirname(os.path.dirname(__file__))
        cache_dir = os.path.join(base_dir, "cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
            
        gallery_dir = os.path.join(cache_dir, str(gid))
        
        try:
            # 1. 获取信息 (带重试)
            result = None
            for retry in range(3):
                try:
                    # 单本子指定分析，不应进行页数过滤，传入 min_pages=0
                    result = await self.crawler.get_gallery_images(gid, min_pages=0)
                    if result:
                        break
                except Exception as e:
                    logger.debug(f"获取本子信息失败 {gid}: {e} (重试 {retry+1}/3)")
                    if retry < 2:
                        await asyncio.sleep(2)
            
            if not result:
                logger.warning(f"无法获取本子信息 {gid} (已重试3次)")
                return None
                
            image_urls, metadata = result
            
            # 构造 gallery 对象
            gallery = {
                'id': gid,
                'title': metadata.get('title', f"Gallery {gid}"),
                'tags': metadata.get('tags', []),
                'gallery_dir': gallery_dir
            }
            
            # 2. 下载图片
            await self.downloader.download_images(image_urls, gallery_dir)
            
            # 3. 分析
            stop_event = threading.Event()
            score, nsfw_stats = await asyncio.to_thread(self.analyzer.analyze_folder, gallery_dir, stop_event)
            gallery['score'] = score
            gallery['stats'] = nsfw_stats
            
            # 4. 提取封面
            cover_found = False
            for ext in ['jpg', 'png', 'webp']:
                cover_path = os.path.join(gallery_dir, f"1.{ext}")
                if os.path.exists(cover_path):
                     saved_cover = os.path.join(cache_dir, f"cover_{gid}.{ext}")
                     if os.path.exists(saved_cover):
                         try: os.remove(saved_cover)
                         except: pass
                     
                     shutil.move(cover_path, saved_cover)
                     gallery['local_cover'] = saved_cover
                     cover_found = True
                     break
            
            # 5. 生成卡片
            output_path = os.path.join(cache_dir, f"nh_{gid}.jpg")
            # render_card 接收列表，我们传入单个元素的列表
            final_card = self.renderer.render_card([gallery], output_path)
            
            # 清理封面
            if cover_found:
                try: os.remove(gallery['local_cover'])
                except: pass
                
            return final_card
            
        except Exception as e:
            logger.error(f"处理单个本子出错 {gid}: {e}")
            return None
        finally:
            # 清理临时目录
            if os.path.exists(gallery_dir):
                shutil.rmtree(gallery_dir, ignore_errors=True)
