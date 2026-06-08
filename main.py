import asyncio
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
from .core.manager import DailyManager


@register(
    "astrbot_plugin_daily_nhentai",
    "s11IM",
    "NHentai 中文列表推荐插件",
    "1.5.0",
    "https://github.com/s11IM/astrbot_plugin_daily_nhentai",
)
class DailyNHentaiPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.manager = DailyManager(context, config)

    @filter.command("nh")
    async def nh(self, event: AstrMessageEvent, message: str = ""):
        """NHentai 助手
        使用方法:
        /nh recent   - 获取中文最新列表（默认）
        /nh today    - 获取今日中文热门
        /nh <id>     - 分析指定 ID 的本子 (例如 /nh 123456)
        """
        cmd = message.strip().lower()
        if not cmd:
            cmd = "recent"

        if cmd in ("recent", "today"):
            source = cmd
            source_label = "中文最新列表" if source == "recent" else "今日中文热门"
            try:
                cached_result = self.manager.get_cached_daily_result(source)
                if cached_result:
                    send_path = self.manager.prepare_image_for_send(cached_result)
                    yield event.chain_result([Image.fromFileSystem(send_path)])
                    return

                yield event.plain_result(
                    f"正在获取{source_label}候选本子，请稍候...这可能需要几分钟甚至更久。"
                )

                # 列表模式设置整体超时20分钟，单本子分析5分钟。
                result_card = await self.manager.process_daily_ranking(
                    source=source,
                    total_timeout=1200,  # 20分钟整体超时
                    analyze_timeout=300,  # 5分钟单本子分析超时
                )
                if result_card:
                    send_path = self.manager.prepare_image_for_send(result_card)
                    yield event.chain_result([Image.fromFileSystem(send_path)])
                else:
                    yield event.plain_result(
                        "未能生成推荐结果，可能是网络问题、暂无数据，或候选本子都被页数过滤，请检查日志或稍后再试。"
                    )
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
                    send_path = self.manager.prepare_image_for_send(result_card)
                    yield event.chain_result([Image.fromFileSystem(send_path)])
                else:
                    yield event.plain_result(
                        "任务失败：可能是网络问题，或页数不符合过滤条件，请检查日志。"
                    )
            except Exception as e:
                yield event.plain_result(f"❌ 处理过程中发生错误: {str(e)}")

        else:
            yield event.plain_result("未知指令。请使用 /nh recent、/nh today 或 /nh <id>")
