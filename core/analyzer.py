import os
import glob
from PIL import Image
import torch
from transformers import pipeline
from astrbot.api import logger

class NSFWAnalyzer:
    def __init__(self, model_dir, threshold=0.15, device=""):
        self.model_dir = model_dir
        self.threshold = threshold
        self.device = device
        self.classifier = None
        self.model_type = None # 'transformers' or 'yolo'

    def _load_model(self):
        """
        懒加载模型
        """
        if self.classifier is not None:
            return

        logger.debug("正在加载 NSFW 检测模型...")
        model_dir = self.model_dir

        # 1. 尝试检测 Transformers 模型
        try:
            subdirs = [d for d in os.listdir(model_dir) if os.path.isdir(os.path.join(model_dir, d))]
            hf_model_path = None
            
            if subdirs:
                potential_path = os.path.join(model_dir, subdirs[0])
                if os.path.exists(os.path.join(potential_path, "config.json")):
                    hf_model_path = potential_path
            elif os.path.exists(os.path.join(model_dir, "config.json")):
                hf_model_path = model_dir

            if hf_model_path:
                logger.debug(f"检测到 Transformers 模型: {hf_model_path}")
                try:
                    device_id = -1
                    if self.device == "cuda" and torch.cuda.is_available():
                        device_id = 0
                    elif self.device == "cpu":
                        device_id = -1
                    else:
                        device_id = 0 if torch.cuda.is_available() else -1
                    
                    self.classifier = pipeline("image-classification", model=hf_model_path, device=device_id)
                    self.model_type = 'transformers'
                    logger.debug(f"Transformers 模型加载成功 (Device: {device_id})")
                    return
                except Exception as e:
                    logger.error(f"Transformers 模型加载失败: {e}")
        except Exception as e:
            logger.debug(f"Transformers 检测出错: {e}")

        # 2. 如果不是 Transformers，尝试 YOLO
        try:
            from ultralytics import YOLO
            pt_files = glob.glob(os.path.join(model_dir, "*.pt"))
            if pt_files:
                model_path = pt_files[0]
                logger.debug(f"检测到 YOLO 模型: {model_path}")
                self.classifier = YOLO(model_path)
                self.model_type = 'yolo'
                logger.debug("YOLO 模型加载成功")
                return
        except ImportError:
            logger.warning("未安装 ultralytics，跳过 YOLO 检测")
        except Exception as e:
            logger.error(f"YOLO 模型加载失败: {e}")

        logger.warning("未能加载任何模型。将无法进行评分。")

    def analyze_folder(self, folder_path, stop_event=None):
        """
        分析文件夹中的所有图片
        
        Args:
            folder_path: 文件夹路径
            stop_event: 可选，用于检测是否需要中断分析 (threading.Event)
        """
        if self.classifier is None:
            self._load_model()

        image_extensions = ['*.jpg', '*.png', '*.jpeg', '*.gif', '*.webp']
        image_files = []
        for ext in image_extensions:
            image_files.extend(glob.glob(os.path.join(folder_path, ext)))
            
        total_pages = len(image_files)
        if total_pages == 0:
            return 0, {}

        if self.classifier is None:
            return 0, {"error": "No model loaded"}

        hentai_pages = 0
        
        # 关键词，根据模型不同可能需要调整
        # EraX 这类模型通常会有 explicit, nsfw, porn 等标签
        nsfw_keywords = ['nsfw', 'porn', 'hentai', 'sexual', 'explicit', 'sex']
        
        logger.debug(f"正在分析 {total_pages} 张图片...")
        for img_path in image_files:
            # 检查停止信号
            if stop_event and stop_event.is_set():
                logger.warning("分析任务中断")
                return 0, {"error": "Interrupted"}

            try:
                is_nsfw = False
                
                # 验证图片有效性
                try:
                    with Image.open(img_path) as img:
                        img.verify()
                except Exception as e:
                    logger.warning(f"跳过无效图片 {os.path.basename(img_path)}: {e}")
                    continue

                if self.model_type == 'transformers':
                    # Transformers Pipeline 推理
                    # 需要将图片文件转换为 Image 对象
                    with Image.open(img_path) as img:
                        results = self.classifier(img)
                    
                    # results 是一个列表 [{'label': 'nsfw', 'score': 0.99}, ...]
                    # 检查 top1
                    if results:
                        top = results[0]
                        label = top['label'].lower()
                        score = top['score']
                        # 调试输出
                        # print(f"{os.path.basename(img_path)}: {label} ({score:.2f})")
                        
                        if any(k in label for k in nsfw_keywords) and score > self.threshold:
                            is_nsfw = True
                        elif label == 'normal' or label == 'safe':
                            is_nsfw = False
                        else:
                            # 如果 top1 不是 safe 且分数很高，也算
                            if score > 0.8:
                                is_nsfw = True
                                
                elif self.model_type == 'yolo':
                    # YOLO 推理使用配置的阈值
                    # device 参数 YOLO 会自动处理，或者我们可以显式传入 device=self.device (如果非空)
                    kwargs = {'verbose': False, 'conf': self.threshold}
                    if self.device:
                        kwargs['device'] = self.device
                        
                    results = self.classifier(img_path, **kwargs)
                    for r in results:
                        # 分类模式
                        if hasattr(r, 'probs') and r.probs is not None:
                            top1 = r.probs.top1
                            label = r.names[top1].lower()
                            if any(k in label for k in nsfw_keywords):
                                is_nsfw = True
                        # 检测模式
                        elif hasattr(r, 'boxes'):
                            for box in r.boxes:
                                cls_id = int(box.cls[0])
                                label = r.names[cls_id].lower()
                                conf = float(box.conf[0])
                                logger.debug(f"  - 检测到: {label} (置信度: {conf:.2f})") # 调试输出

                                # EraX-NSFW-V1.0 模型定义的标签:
                                # anus, make_love, nipple, penis, vagina
                                
                                # 只要检测到 make_love (交合) 或 penis/vagina/anus (关键部位) 即视为 NSFW 页
                                # nipple 单独出现可能只是擦边，但也计入 NSFW
                                keywords = [
                                    'make_love',
                                    'penis',
                                    'vagina',
                                    'anus',
                                    'nipple'
                                ]
                                if any(k in label for k in keywords):
                                    is_nsfw = True
                                    logger.debug(f"    -> 判定为 NSFW 目标")
                                    break

                if is_nsfw:
                    hentai_pages += 1
                    
            except Exception as e:
                logger.warning(f"处理图片 {img_path} 出错: {e}")

        score = (hentai_pages / total_pages) * 100
        stats = {"total": total_pages, "hentai": hentai_pages}
        
        return score, stats