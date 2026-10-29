"""
人脸识别模块：支持会员注册和实时识别。
使用 face_recognition 库，并提供降级方案。
"""

import logging
import numpy as np
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session

from agent.models import Member

logger = logging.getLogger(__name__)

# 尝试导入 face_recognition，如果失败则使用降级方案
try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    logger.warning("face_recognition not available, using fallback")


class FaceRecognizer:
    """
    人脸识别器：支持注册和实时比对
    如果 face_recognition 不可用，使用降级方案（模拟）
    """
    
    # 相似度阈值（距离越小越相似）
    SIMILARITY_THRESHOLD = 0.6
    
    def __init__(self, db_session: Session, cache_encodings: bool = True):
        self.db = db_session
        self.cache_encodings = cache_encodings
        self._encoding_cache: Dict[str, np.ndarray] = {}
        self._all_encodings: List[Tuple[str, np.ndarray]] = []
        self._cache_loaded = False
    
    def _load_all_encodings(self):
        """从数据库加载所有会员的人脸编码到缓存"""
        if self._cache_loaded and self.cache_encodings:
            return
        
        try:
            members = self.db.query(Member).filter(
                Member.face_encoding.isnot(None)
            ).all()
            
            self._all_encodings = []
            for m in members:
                if m.face_encoding:
                    try:
                        # 从bytes转换为numpy数组
                        encoding = np.frombuffer(m.face_encoding, dtype=np.float64)
                        if len(encoding) == 128:  # face_recognition 标准维度
                            self._all_encodings.append((m.id, encoding))
                            self._encoding_cache[m.id] = encoding
                    except Exception as e:
                        logger.warning(f"Failed to decode face encoding for {m.id}: {e}")
            
            self._cache_loaded = True
            logger.info(f"Loaded {len(self._all_encodings)} face encodings")
            
        except Exception as e:
            logger.error(f"Failed to load face encodings: {e}")
    
    def register_member(
        self,
        member_id: str,
        image_path: Optional[str] = None,
        image_array: Optional[np.ndarray] = None,
    ) -> bool:
        """
        注册会员人脸
        
        Args:
            member_id: 会员ID
            image_path: 图片路径（与 image_array 二选一）
            image_array: 图片数组（与 image_path 二选一）
        """
        if not FACE_RECOGNITION_AVAILABLE:
            logger.warning("face_recognition not available, skip registration")
            return False
        
        try:
            # 加载图片
            if image_path:
                image = face_recognition.load_image_file(image_path)
            elif image_array is not None:
                image = image_array
            else:
                logger.error("No image provided")
                return False
            
            # 检测人脸
            face_locations = face_recognition.face_locations(image)
            if not face_locations:
                logger.error(f"No face found in image for member {member_id}")
                return False
            
            # 提取编码
            encodings = face_recognition.face_encodings(image, face_locations)
            if not encodings:
                logger.error(f"Failed to extract encoding for member {member_id}")
                return False
            
            encoding = encodings[0]
            encoding_bytes = encoding.tobytes()
            
            # 更新数据库
            member = self.db.query(Member).filter(Member.id == member_id).first()
            if not member:
                logger.error(f"Member {member_id} not found")
                return False
            
            member.face_encoding = encoding_bytes
            self.db.commit()
            
            # 更新缓存
            self._encoding_cache[member_id] = encoding
            self._all_encodings.append((member_id, encoding))
            self._cache_loaded = True
            
            logger.info(f"Registered face for member {member_id}")
            return True
            
        except Exception as e:
            logger.error(f"Face registration error: {e}")
            self.db.rollback()
            return False
    
    def recognize_face(
        self,
        image: np.ndarray,
        threshold: Optional[float] = None,
        allow_fallback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        在图像中检测人脸并尝试匹配会员

        Args:
            image: 图像数组（BGR格式）
            threshold: 相似度阈值（默认0.6）
            allow_fallback: 是否允许降级 mock 结果（默认 False，生产环境应禁用）

        Returns:
            会员信息字典或None。降级结果始终包含 is_fallback=True 标记。
        """
        if not FACE_RECOGNITION_AVAILABLE:
            if allow_fallback:
                return self._fallback_recognition(image)
            logger.warning("face_recognition not available and fallback disabled, returning None")
            return None

        threshold = threshold or self.SIMILARITY_THRESHOLD

        try:
            # 确保缓存加载
            self._load_all_encodings()

            if not self._all_encodings:
                return None

            # 检测人脸
            face_locations = face_recognition.face_locations(image)
            if not face_locations:
                return None

            # 提取编码（取第一个脸）
            face_encodings = face_recognition.face_encodings(image, face_locations)
            if not face_encodings:
                return None

            face_encoding = face_encodings[0]

            # 与所有会员编码比较
            known_ids = [m[0] for m in self._all_encodings]
            known_encodings = [m[1] for m in self._all_encodings]

            if not known_encodings:
                return None

            distances = face_recognition.face_distance(known_encodings, face_encoding)
            best_index = int(np.argmin(distances))
            min_distance = float(distances[best_index])

            if min_distance < threshold:
                member_id = known_ids[best_index]
                # 查询会员详细信息
                member = self.db.query(Member).filter(Member.id == member_id).first()
                if member:
                    return {
                        "member_id": member.id,
                        "name": member.name,
                        "phone": member.phone,
                        "tier": member.tier,
                        "points": member.points,
                        "total_spent": float(member.total_spent) if member.total_spent else 0,
                        "confidence": 1 - min_distance,
                        "similarity_distance": min_distance,
                    }

            return None

        except Exception as e:
            logger.error(f"Face recognition error: {e}")
            if allow_fallback:
                return self._fallback_recognition(image)
            return None
    
    def _fallback_recognition(self, image: np.ndarray) -> Optional[Dict[str, Any]]:
        """
        降级识别方案（当 face_recognition 不可用时）
        返回模拟的会员信息用于测试
        """
        logger.warning("Using fallback face recognition (testing mode)")
        # 返回一个模拟的会员信息，方便测试
        # 实际生产环境应该禁用或使用其他方案
        return {
            "member_id": "test_member_001",
            "name": "测试会员",
            "tier": "gold",
            "points": 1000,
            "confidence": 0.85,
            "is_fallback": True,
        }
    
    def delete_encoding(self, member_id: str) -> bool:
        """删除会员的人脸编码"""
        try:
            member = self.db.query(Member).filter(Member.id == member_id).first()
            if member:
                member.face_encoding = None
                self.db.commit()
                
                # 更新缓存
                self._encoding_cache.pop(member_id, None)
                self._all_encodings = [(m_id, enc) for m_id, enc in self._all_encodings if m_id != member_id]
                
                logger.info(f"Deleted face encoding for {member_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete encoding: {e}")
            self.db.rollback()
            return False