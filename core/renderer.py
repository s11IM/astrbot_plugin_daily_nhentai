from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import os
import textwrap

class ResultRenderer:
    def __init__(self):
        # 字体回退机制 (支持 Windows 和 Linux)
        self.font_path = None
        
        # 候选字体列表
        base_dir = os.path.dirname(os.path.dirname(__file__))
        
        candidate_fonts = [
            # 优先检查插件根目录下的字体
            os.path.join(base_dir, "fonts", "msyh.ttc"),
            os.path.join(base_dir, "msyh.ttc"),
            os.path.join(base_dir, "SimHei.ttf"),
            os.path.join(base_dir, "arial.ttf"),
            
            # Windows Fonts
            "C:/Windows/Fonts/msyhbd.ttc", # 微软雅黑 粗体
            "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf", # 黑体
            # Linux Fonts (常见于 Ubuntu/Debian/CentOS)
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",   # 文泉驿微米黑
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",     # 文泉驿正黑
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", # Noto Sans CJK
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",       # AR PL UMing
            # Fallback
            "arial.ttf"
        ]

        for font in candidate_fonts:
            if os.path.exists(font):
                self.font_path = font
                break
        
        # 如果都没找到，最后尝试加载默认字体（虽然可能不支持中文）
        if self.font_path is None:
            self.font_path = "arial.ttf"

    def add_rounded_corners(self, im, rad):
        """给图片添加圆角"""
        circle = Image.new('L', (rad * 2, rad * 2), 0)
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, rad * 2, rad * 2), fill=255)
        
        mask = Image.new('L', im.size, 255)
        w, h = im.size
        mask.paste(circle.crop((0, 0, rad, rad)), (0, 0))
        mask.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
        mask.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
        mask.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
        
        if im.mode != 'RGBA':
            im = im.convert('RGBA')
            
        from PIL import ImageChops
        orig_alpha = im.split()[3]
        new_alpha = ImageChops.multiply(orig_alpha, mask)
        im.putalpha(new_alpha)
        return im

    def wrap_text_by_width(self, text, font, max_width, draw):
        """根据像素宽度自动换行"""
        lines = []
        if not text:
            return lines
            
        current_line = ""
        for char in text:
            # 尝试添加字符
            test_line = current_line + char
            # 获取宽度
            if hasattr(draw, 'textlength'):
                w = draw.textlength(test_line, font=font)
            else:
                w = font.getlength(test_line)
            
            if w <= max_width:
                current_line = test_line
            else:
                # 超出宽度，换行
                if current_line:
                    lines.append(current_line)
                current_line = char
        
        if current_line:
            lines.append(current_line)
            
        return lines

    def draw_rounded_rect(self, draw, xy, radius, fill, outline=None, outline_width=2):
        """绘制圆角矩形"""
        x1, y1, x2, y2 = xy
        w = x2 - x1
        h = y2 - y1
        
        # 自动调整半径，防止半径过大导致错误
        if radius * 2 > w:
            radius = w // 2
        if radius * 2 > h:
            radius = h // 2
            
        # 主体矩形
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        
        # 四个圆角
        draw.ellipse([x1, y1, x1 + radius * 2, y1 + radius * 2], fill=fill)
        draw.ellipse([x2 - radius * 2, y1, x2, y1 + radius * 2], fill=fill)
        draw.ellipse([x1, y2 - radius * 2, x1 + radius * 2, y2], fill=fill)
        draw.ellipse([x2 - radius * 2, y2 - radius * 2, x2, y2], fill=fill)
        
        if outline:
            draw.arc([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=outline, width=outline_width)
            draw.arc([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=outline, width=outline_width)
            draw.arc([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=outline, width=outline_width)
            draw.arc([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=outline, width=outline_width)
            draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=outline_width)
            draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=outline_width)
            draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=outline_width)
            draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=outline_width)

    def render_single_card(self, gallery, card_w, card_h, card_radius, bg_color, box_bg, box_border,
                          text_white, text_gray, text_light_gray, accent_color, link_color,
                          font_title, font_score, font_link, font_tag, font_label):
        """渲染单个卡片"""
        # 左右分区比例 - 左侧40% 右侧60%
        left_ratio = 0.4
        left_w = int(card_w * left_ratio)
        right_w = card_w - left_w
        
        # 创建主画布
        canvas = Image.new('RGBA', (card_w, card_h), bg_color)
        draw = ImageDraw.Draw(canvas)

        # ========== 左侧区域（纯封面图）==========
        cover_path = gallery.get('local_cover')
        if cover_path and os.path.exists(cover_path):
            try:
                cover = Image.open(cover_path).convert("RGBA")
                # 等比缩放并裁剪填充到左侧区域
                cover_ratio = cover.width / cover.height
                target_ratio = left_w / card_h
                
                if cover_ratio > target_ratio:
                    new_h = card_h
                    new_w = int(new_h * cover_ratio)
                    cover = cover.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    left = (new_w - left_w) // 2
                    cover = cover.crop((left, 0, left + left_w, card_h))
                else:
                    new_w = left_w
                    new_h = int(new_w / cover_ratio)
                    cover = cover.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    top = (new_h - card_h) // 2
                    cover = cover.crop((0, top, left_w, top + new_h))
                
                canvas.paste(cover, (0, 0))
            except Exception as e:
                print(f"封面处理出错: {e}")
                draw.rectangle([0, 0, left_w, card_h], fill=(60, 60, 65))
        else:
            draw.rectangle([0, 0, left_w, card_h], fill=(60, 60, 65))
        
        # 绘制左右分界线
        draw.line([(left_w, 0), (left_w, card_h)], fill=box_border, width=3)
        
        # ========== 右侧区域（四层结构）==========
        right_padding = 20
        box_padding = 18
        box_radius = 10
        gap = 12
        
        # 右侧起始位置
        right_x = left_w + right_padding
        right_y = right_padding
        right_available_w = right_w - right_padding * 2
        
        # 四层总高度 = 卡片高度 - 上下padding
        total_layers_h = card_h - right_padding * 2
        
        # 重新分配高度
        layer3_h = 50   # 实用度层
        layer4_h = 50   # 链接层
        layer2_h = 136  # 标签层（缩小30%）
        # 标题层 = 总高 - 其他三层 - 3个gap
        layer1_h = total_layers_h - layer2_h - layer3_h - layer4_h - gap * 3
        
        # 获取数据
        title = gallery.get('title', 'Unknown Title')
        tags = gallery.get('tags', [])
        score = gallery.get('score', 0)
        gid = gallery.get('id', '???')
        
        # ---- 第一层：标题（最上面）----
        layer1_x = right_x
        layer1_y = right_y
        layer1_w = right_available_w
        
        self.draw_rounded_rect(draw, [layer1_x, layer1_y, layer1_x + layer1_w, layer1_y + layer1_h], 
                               box_radius, box_bg, box_border, 2)
        
        # 标题标签
        draw.text((layer1_x + box_padding, layer1_y + 12), "标题", font=font_label, fill=text_light_gray)
        
        # 标题文字自动换行 (使用像素宽度计算)
        max_text_width = layer1_w - box_padding * 2
        title_lines = self.wrap_text_by_width(title, font_title, max_text_width, draw)
        
        title_y = layer1_y + 36
        max_title_lines = max(3, (layer1_h - 50) // 26)
        for line in title_lines[:max_title_lines]:
            draw.text((layer1_x + box_padding, title_y), line, font=font_title, fill=text_white)
            title_y += 26
        
        # ---- 第二层：标签 ----
        layer2_x = right_x
        layer2_y = layer1_y + layer1_h + gap
        layer2_w = right_available_w
        
        self.draw_rounded_rect(draw, [layer2_x, layer2_y, layer2_x + layer2_w, layer2_y + layer2_h], 
                               box_radius, box_bg, box_border, 2)
        
        # 标签标签
        draw.text((layer2_x + box_padding, layer2_y + 10), "标签", font=font_label, fill=text_light_gray)
        
        # 标签展示
        tag_x = layer2_x + box_padding
        tag_y = layer2_y + 32
        tag_h = 24
        tag_gap_x = 8
        tag_gap_y = 8
        
        # 过滤和排序标签
        important_tags = []
        secondary_tags = []
        for tag in tags[:30]:
            tag_lower = tag.lower()
            if any(k in tag_lower for k in ['chinese', 'translated', 'full color', 'uncensored']):
                important_tags.append(tag)
            else:
                secondary_tags.append(tag)
        
        display_tags = important_tags + secondary_tags
        
        for tag in display_tags:
            try:
                tw = draw.textlength(tag, font=font_tag)
            except:
                tw = len(tag) * 9
            
            chip_w = int(tw) + 14
            chip_h = tag_h
            
            # 检查是否超出边界需要换行
            if tag_x + chip_w > layer2_x + layer2_w - box_padding:
                tag_x = layer2_x + box_padding
                tag_y += chip_h + tag_gap_y
            
            # 检查是否超出框高度
            if tag_y + chip_h > layer2_y + layer2_h - 10:
                break
            
            # 绘制标签背景
            tag_bg_color = (70, 74, 82) if tag in important_tags else (60, 64, 72)
            self.draw_rounded_rect(draw, [tag_x, tag_y, tag_x + chip_w, tag_y + chip_h], 
                                   8, tag_bg_color)
            
            # 绘制标签文字
            draw.text((tag_x + 7, tag_y + 5), tag, font=font_tag, fill=text_gray)
            
            tag_x += chip_w + tag_gap_x
        
        # ---- 第三层：实用度（移到最底部上方）----
        layer3_x = right_x
        layer3_y = right_y + total_layers_h - layer4_h - gap - layer3_h
        layer3_w = right_available_w
        
        self.draw_rounded_rect(draw, [layer3_x, layer3_y, layer3_x + layer3_w, layer3_y + layer3_h], 
                               box_radius, box_bg, box_border, 2)
        
        # CB指数显示
        if isinstance(score, (int, float)):
            score_text = f"CB指数：{int(score)}%"
        else:
            score_text = "CB指数：N/A"
        
        bbox = draw.textbbox((0, 0), score_text, font=font_score)
        text_h = bbox[3] - bbox[1]
        text_y = layer3_y + (layer3_h - text_h) // 2 - 2
        draw.text((layer3_x + box_padding, text_y), score_text, font=font_score, fill=accent_color)
        
        # ---- 第四层：本子链接（最底部）----
        layer4_x = right_x
        layer4_y = right_y + total_layers_h - layer4_h
        layer4_w = right_available_w
        
        self.draw_rounded_rect(draw, [layer4_x, layer4_y, layer4_x + layer4_w, layer4_y + layer4_h], 
                               box_radius, box_bg, box_border, 2)
        
        # 链接显示
        link_text = f"nhentai.net/g/{gid}/"
        bbox = draw.textbbox((0, 0), link_text, font=font_link)
        text_h = bbox[3] - bbox[1]
        text_y = layer4_y + (layer4_h - text_h) // 2 - 2
        draw.text((layer4_x + box_padding, text_y), link_text, font=font_link, fill=link_color)
        
        # 给整个卡片添加圆角
        canvas = self.add_rounded_corners(canvas, card_radius)
        
        return canvas

    def render_card(self, galleries, output_path):
        if not galleries:
            return None

        # 卡片尺寸
        card_w, card_h = 1000, 500
        card_radius = 20
        
        # 颜色方案
        bg_color = (30, 33, 40)
        box_bg = (55, 59, 67)
        box_border = (80, 84, 92)
        text_white = (255, 255, 255)
        text_gray = (180, 185, 195)
        text_light_gray = (140, 145, 155)
        accent_color = (255, 100, 120)
        link_color = (100, 180, 255)
        
        # 加载字体
        try:
            font_title = ImageFont.truetype(self.font_path, 24)
            font_score = ImageFont.truetype(self.font_path, 22)
            font_link = ImageFont.truetype(self.font_path, 18)
            font_tag = ImageFont.truetype(self.font_path, 13)
            font_label = ImageFont.truetype(self.font_path, 14)
        except:
            font_title = ImageFont.load_default()
            font_score = ImageFont.load_default()
            font_link = ImageFont.load_default()
            font_tag = ImageFont.load_default()
            font_label = ImageFont.load_default()
        
        # 渲染所有卡片
        cards = []
        for gallery in galleries:
            card = self.render_single_card(
                gallery, card_w, card_h, card_radius, bg_color, box_bg, box_border,
                text_white, text_gray, text_light_gray, accent_color, link_color,
                font_title, font_score, font_link, font_tag, font_label
            )
            cards.append(card)
        
        # 拼接卡片
        if len(cards) == 1:
            final_canvas = cards[0]
        elif len(cards) <= 5:
            # 单列布局 (原始逻辑)
            spacing = 30
            divider_height = 4  # 分界线高度
            divider_color = (100, 104, 112)  # 分界线颜色（比背景亮一些）
            
            # 计算总高度：所有卡片 + 间距 + 分界线 + 上下边距
            total_height = (sum(c.height for c in cards) +
                           spacing * (len(cards) - 1) +
                           divider_height * (len(cards) - 1) +
                           spacing * 2)
            
            final_canvas = Image.new('RGBA', (card_w + spacing * 2, total_height), bg_color)
            draw = ImageDraw.Draw(final_canvas)
            
            y_offset = spacing
            for i, card in enumerate(cards):
                final_canvas.paste(card, (spacing, y_offset), card)
                y_offset += card.height
                
                # 如果不是最后一张卡片，绘制分界线
                if i < len(cards) - 1:
                    # 绘制水平分界线
                    divider_y = y_offset + spacing // 2 - divider_height // 2
                    draw.rectangle(
                        [spacing + 20, divider_y, card_w + spacing - 20, divider_y + divider_height],
                        fill=divider_color
                    )
                    y_offset += spacing + divider_height
        else:
            # 双列布局 (>5 个本子)
            spacing = 30
            column_spacing = 50 # 左右列之间的间距
            divider_height = 4
            divider_color = (100, 104, 112)
            
            # 分割卡片为左右两列
            left_cards = cards[:5]
            right_cards = cards[5:]
            
            # 计算单列的高度
            def calculate_column_height(column_cards):
                if not column_cards: return 0
                return (sum(c.height for c in column_cards) +
                        spacing * (len(column_cards) - 1) +
                        divider_height * (len(column_cards) - 1) +
                        spacing * 2)

            left_height = calculate_column_height(left_cards)
            right_height = calculate_column_height(right_cards)
            
            total_height = max(left_height, right_height)
            total_width = (card_w * 2) + (spacing * 2) * 2 + column_spacing # 两列宽 + 两列内边距 + 列间距
            
            final_canvas = Image.new('RGBA', (total_width, total_height), bg_color)
            draw = ImageDraw.Draw(final_canvas)
            
            # 绘制列函数
            def draw_column(column_cards, x_start):
                y_offset = spacing
                for i, card in enumerate(column_cards):
                    final_canvas.paste(card, (x_start + spacing, y_offset), card)
                    y_offset += card.height
                    
                    if i < len(column_cards) - 1:
                        divider_y = y_offset + spacing // 2 - divider_height // 2
                        draw.rectangle(
                            [x_start + spacing + 20, divider_y, x_start + card_w + spacing - 20, divider_y + divider_height],
                            fill=divider_color
                        )
                        y_offset += spacing + divider_height

            # 绘制左列
            draw_column(left_cards, 0)
            
            # 绘制右列 (如果右列比左列短，可能会留白，但这通常没问题)
            if right_cards:
                draw_column(right_cards, card_w + spacing * 2 + column_spacing)
            
            # 在两列中间绘制一条垂直分割线
            center_x = card_w + spacing * 2 + column_spacing // 2
            draw.line([(center_x, spacing), (center_x, total_height - spacing)], fill=divider_color, width=4)

        # 转换为RGB并保存
        final_image = final_canvas.convert('RGB')
        final_image.save(output_path, quality=95)
        return output_path
