import asyncio
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from .core.manager import DailyManager


@register("astrbot_plugin_daily_nhentai", "s11IM", "NHentai 每日流行推荐插件", "1.5.0",
          "https://github.com/s11IM/astrbot_plugin_daily_nhentai")
class DailyNHentaiPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.manager = DailyManager(context, config)

    @filter.command("nh")
    async def nh(self, event: AstrMessageEvent, message: str = ""):
        '''NHentai 助手
        使用方法:
        /nh today    - 获取今日中文热门（默认）
        /nh <id>     - 分析指定 ID 的本子 (例如 /nh 123456)
        '''
        cmd = message.strip()
        
        if not cmd or cmd == "today":
            # === 今日热门逻辑 ===
            yield event.plain_result("正在获取今日热门的25个本子，请稍候...这可能需要几分钟甚至更久。")
            try:
                # 调用处理函数，设置整体超时20分钟，单本子分析5分钟
                result_card = await self.manager.process_daily_ranking(
                    total_timeout=1200,  # 20分钟整体超时
                    analyze_timeout=300  # 5分钟单本子分析超时
                )
                if result_card:
                    yield event.chain_result([
                        Image.fromFileSystem(result_card)
                    ])
                else:
                    yield event.plain_result("未能生成推荐结果，可能是网络问题或暂无数据，请检查日志或稍后再试。")
            except asyncio.TimeoutError as e:
                yield event.plain_result(f"⏱️ {str(e)}")
            except Exception as e:
                yield event.plain_result(f"❌ 处理过程中发生错误: {str(e)}")
        
        elif cmd.isdigit():
            # === 单个本子分析逻辑 ===
            gid = int(cmd)
            yield event.plain_result(f"正在分析本子 {gid}，请稍候...")
            
            try:
                result_card = await self.manager.process_single_gallery(gid)
                
                if result_card:
                    yield event.chain_result([
                        Image.fromFileSystem(result_card)
                    ])
                else:
                    yield event.plain_result(f"任务失败：请检查日志")
            except Exception as e:
                yield event.plain_result(f"❌ 处理过程中发生错误: {str(e)}")
        
        else:
            yield event.plain_result("未知指令。请使用 /nh today 或 /nh <id>")
