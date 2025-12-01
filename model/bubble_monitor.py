# bubble_monitor.py
import numpy as np
import cv2
import matplotlib.pyplot as plt
from datetime import datetime
import os

# ————————————————————————————————
# 1️⃣ 假数据生成器（模拟传感器 + 培养皿图像）
# ————————————————————————————————
class FakeSensorSimulator:
    def __init__(self, seed=42):
        np.random.seed(seed)
        self.base_temp = 37.0
        self.base_light = 0.5
        self.time = 0.0  # 模拟时间（min）

    def generate_sensor_data(self):
        """返回 (temp, light)"""
        # 模拟缓慢漂移 + 噪声
        drift = 0.01 * np.sin(0.001 * self.time)
        temp = self.base_temp + drift + np.random.normal(0, 0.2)
        light = np.clip(self.base_light + 0.3 * np.sin(0.005 * self.time) + np.random.normal(0, 0.05), 0, 1)
        self.time += 1
        return round(temp, 2), round(light, 3)

    def generate_bubble_image(self, temp, light, save_path=None):
        """
        根据 temp & light 生成带气泡的模拟培养皿图像（512x512）
        气泡数量/大小受温光影响 → 高温/强光 → 更多大气泡
        """
        h, w = 512, 512
        img = np.ones((h, w), dtype=np.uint8) * 240  # 浅灰背景（模拟培养基）

        # 中心画培养皿圆形区域（半径200）
        cv2.circle(img, (w//2, h//2), 200, 220, -1)  # 更暗底色

        # 气泡生成逻辑：高温或强光 → 更多气泡
        bubble_factor = max(0, (temp - 35) / 5 + (light - 0.4) / 0.6)  # 0~1+
        n_bubbles = int(np.clip(np.random.poisson(lam=2 + 8 * bubble_factor), 0, 20))
        
        for _ in range(n_bubbles):
            # 随机位置（在培养皿内）
            r = np.random.randint(5, 30)
            angle = np.random.rand() * 2 * np.pi
            dist = np.random.rand() * (180 - r)
            cx = int(w//2 + dist * np.cos(angle))
            cy = int(h//2 + dist * np.sin(angle))

            # 气泡：亮环 + 暗核（模拟反光）
            cv2.circle(img, (cx, cy), r, 255, -1)      # 白心
            cv2.circle(img, (cx, cy), r, 100, max(1, r//5))  # 暗边

        # 添加少量椒盐噪声模拟相机噪声
        noise = np.random.rand(h, w) < 0.005
        img[noise] = 0
        img[~noise & (np.random.rand(h, w) < 0.005)] = 255

        if save_path:
            cv2.imwrite(save_path, img)
        return img


# ————————————————————————————————
# 2️⃣ 气泡识别模型（图像 → bubble_ratio）
# ————————————————————————————————
def detect_bubble_ratio(img):
    """
    输入：灰度图 (H, W)
    输出：气泡面积占比 (float, 0~1)
    方法：自适应阈值 + 开运算去噪 + 轮廓面积统计
    """
    if len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 高斯模糊降噪
    blurred = cv2.GaussianBlur(img, (5, 5), 0)

    # 自适应阈值（气泡通常亮于背景）
    thresh = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21, 5
    )

    # 形态学开运算（去小噪点）
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    # 查找轮廓（仅大轮廓视为气泡）
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = 100  # >10x10 像素
    bubble_area = sum(cv2.contourArea(c) for c in contours if cv2.contourArea(c) > min_area)

    total_area = img.shape[0] * img.shape[1]
    ratio = bubble_area / total_area
    return min(ratio, 1.0)  # 防越界


# ————————————————————————————————
# 3️⃣ 决策模型（温、光、气泡 → 建议）
# ————————————————————————————————
def decide_adjustment(temp, light, bubble_ratio,
                      temp_thresh=37.0,
                      light_thresh=0.65,
                      bubble_thresh=0.03):
    """
    规则引擎决策（可替换为训练好的 sklearn/XGBoost 模型）
    """
    if bubble_ratio <= bubble_thresh:
        return "no_adjust", 0

    # 计算偏离度（用于排序优先级）
    temp_dev = max(0, temp - temp_thresh)
    light_dev = max(0, light - light_thresh)

    if temp_dev > 0.5 and light_dev > 0.1:
        return "temp↓", temp_dev
    elif temp_dev >= light_dev:
        return "temp↓", temp_dev
    elif light_dev > 0:
        return "light↓", light_dev
    else:
        return "unknown", 0


# ————————————————————————————————
# 4️⃣ 主流程 & 可视化
# ————————————————————————————————
def main(n_samples=5, visualize=True):
    sim = FakeSensorSimulator(seed=2025)
    os.makedirs("sim_images", exist_ok=True)

    print("="*60)
    print("🔬 培养皿气泡智能监测系统（模拟版）")
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    for i in range(n_samples):
        # 1. 生成传感器数据
        temp, light = sim.generate_sensor_data()

        # 2. 生成图像
        img_path = f"sim_images/sample_{i:02d}.png"
        img = sim.generate_bubble_image(temp, light, save_path=img_path)

        # 3. 检测气泡
        bubble_ratio = detect_bubble_ratio(img)

        # 4. 决策
        action, score = decide_adjustment(temp, light, bubble_ratio)

        # 5. 输出
        print(f"\n【样本 #{i+1}】")
        print(f"  🌡️ 温度：{temp:.2f}°C")
        print(f"  💡 光照：{light:.3f} (0~1)")
        print(f"  🫧 气泡占比：{bubble_ratio:.2%}")
        print(f"  🤖 建议：", end="")

        tips = {
            "temp↓": "降低温度",
            "light↓": "减弱光照",
            "no_adjust": "正常，无需干预",
            "unknown": "异常增多！检查污染/震动"
        }
        print(f"{tips[action]} {'(置信度高)' if score > 0.5 else ''}")

        # 可选：显示图像（前2个样本）
        if visualize and i < 2:
            plt.figure(figsize=(8, 4))
            plt.subplot(1, 2, 1)
            plt.imshow(img, cmap='gray')
            plt.title(f'培养皿图像\n🌡️{temp}°C | 💡{light:.2f}')
            plt.axis('off')

            # 叠加检测结果
            img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            contours, _ = cv2.findContours(
                cv2.morphologyEx(
                    cv2.adaptiveThreshold(cv2.GaussianBlur(img, (5,5),0),255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,21,5),
                    cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))
                ),
                cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            cv2.drawContours(img_bgr, [c for c in contours if cv2.contourArea(c) > 100], -1, (0,255,0), 2)
            plt.subplot(1, 2, 2)
            plt.imshow(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
            plt.title(f'气泡检测结果\n占比：{bubble_ratio:.1%}')
            plt.axis('off')
            plt.tight_layout()
            plt.show()

    print("\n✅ 模拟完成！图像已保存至 `./sim_images/`")


# ————————————————————————————————
# 🚀 运行入口
# ————————————————————————————————
if __name__ == "__main__":
    main(n_samples=5, visualize=True)