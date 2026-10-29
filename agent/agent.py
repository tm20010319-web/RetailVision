"""
视觉智能体子图：整合视频分析、会员识别、推荐
响应主Agent的查询
"""

import logging
from typing import Dict, Any, Optional, TypedDict
from datetime import datetime, timedelta
from langgraph.graph import StateGraph, END

from agent.detector import VisionDetector
from agent.face_recognizer import FaceRecognizer
from agent.member_service import MemberService
from agent.recommendation import RecommendationEngine
from agent.db import get_db_session

from agent import customer_profile_analyzer


class AgentState(TypedDict, total=False):
    """LangGraph 基础状态，替代原先不存在的 backend.core.graph.state.AgentState。"""
    current_step: str

logger = logging.getLogger(__name__)


class VisionAgentState(AgentState):
    """视觉子图状态"""
    query: str
    member_id: Optional[str] = None
    member_info: Optional[Dict[str, Any]] = None
    analysis_result: Optional[Dict[str, Any]] = None
    recommendations: list = []
    coupons: list = []
    final_answer: str = ""


# ----- 节点函数 -----

async def analyze_video_node(state: VisionAgentState) -> Dict[str, Any]:
    """
    分析视频数据（客流、区域热度等）
    """
    try:
        # 获取查询中的时间范围
        query = state.get("query", "")
        days = 1
        if "本月" in query:
            days = 30
        elif "本周" in query:
            days = 7
        elif "今天" in query:
            days = 1
        
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        # 使用Detector分析（实际使用时，视频源从配置读取）
        detector = VisionDetector()
        detector.initialize()

        # 示例：分析一个测试视频，实际应该从摄像头流获取
        # 此处模拟返回结果
        result = {
            "total_visits": 156,
            "unique_visitors": 89,
            "zone_heatmap": {
                "electronics": 45,
                "clothing": 32,
                "checkout": 18,
                "entrance": 5,
            },
            "peak_hours": [10, 11, 14, 15, 16],
            "period": f"{start_time.strftime('%Y-%m-%d')} 至 {end_time.strftime('%Y-%m-%d')}",
        }
        
        return {
            "analysis_result": result,
            "current_step": "video_analyzed",
        }
    except Exception as e:
        logger.error(f"Video analysis error: {e}")
        return {
            "analysis_result": {"error": str(e)},
            "current_step": "video_analysis_failed",
        }


async def recognize_member_node(state: VisionAgentState) -> Dict[str, Any]:
    """
    从摄像头画面识别会员
    """
    try:
        db = get_db_session()
        recognizer = FaceRecognizer(db)
        
        # 实际场景：从摄像头获取当前帧
        # 此处模拟：从查询中提取会员ID（测试用）
        query = state.get("query", "")
        if "会员" in query:
            # 模拟识别到一个会员
            member_info = {
                "member_id": "M001",
                "name": "张三",
                "tier": "gold",
                "points": 1500,
                "total_spent": 1280.00,
                "confidence": 0.95,
            }
            return {
                "member_info": member_info,
                "member_id": "M001",
                "current_step": "member_recognized",
            }
        else:
            # 未识别到会员
            return {
                "member_info": None,
                "current_step": "no_member",
            }
    except Exception as e:
        logger.error(f"Face recognition error: {e}")
        return {
            "member_info": None,
            "current_step": "recognition_failed",
        }


async def member_service_node(state: VisionAgentState) -> Dict[str, Any]:
    """
    查询会员信息、历史、偏好
    """
    member_id = state.get("member_id")
    if not member_id:
        return {"current_step": "no_member_data"}
    
    try:
        db = get_db_session()
        service = MemberService(db)
        
        # 获取会员信息
        profile = service.get_member_by_id(member_id)
        history = service.get_purchase_history(member_id, days=30)
        preferences = service.get_preferences(member_id)
        visits = service.get_visit_history(member_id, days=30)
        
        # 记录到店
        service.record_visit(member_id, "entrance", confidence=0.95)
        
        return {
            "member_profile": profile,
            "purchase_history": history,
            "preferences": preferences,
            "visit_history": visits,
            "current_step": "member_data_fetched",
        }
    except Exception as e:
        logger.error(f"Member service error: {e}")
        return {"error": str(e), "current_step": "member_service_failed"}


async def recommend_node(state: VisionAgentState) -> Dict[str, Any]:
    """
    基于会员数据和当前区域生成推荐，发放优惠券
    """
    member_id = state.get("member_id")
    if not member_id:
        return {"current_step": "no_recommendation"}
    
    try:
        db = get_db_session()
        engine = RecommendationEngine(db)
        
        # 获取当前区域（从分析结果中推断）
        analysis = state.get("analysis_result", {})
        zone = "electronics"  # 默认区域，实际应从摄像头分析获取
        
        result = engine.process_member_entry(member_id, zone)
        
        # 发放优惠券
        coupons = result.get("coupons", [])
        
        return {
            "recommendations": result.get("recommendations", []),
            "coupons": coupons,
            "current_step": "recommendation_done",
        }
    except Exception as e:
        logger.error(f"Recommendation error: {e}")
        return {"error": str(e), "current_step": "recommendation_failed"}


async def format_response_node(state: VisionAgentState) -> Dict[str, Any]:
    """
    格式化最终输出为自然语言
    """
    member_info = state.get("member_info", {})
    analysis = state.get("analysis_result", {})
    recommendations = state.get("recommendations", [])
    coupons = state.get("coupons", [])
    
    answer_parts = []
    
    # 1. 客流分析
    if analysis and "error" not in analysis:
        answer_parts.append(f"📊 客流分析（{analysis.get('period', '')}）：")
        answer_parts.append(f"  - 总到店人数：{analysis.get('total_visits', 0)} 人次")
        answer_parts.append(f"  - 独立访客：{analysis.get('unique_visitors', 0)} 人")
        if analysis.get("zone_heatmap"):
            answer_parts.append("  - 区域热度：")
            for zone, count in analysis.get("zone_heatmap", {}).items():
                answer_parts.append(f"    • {zone}：{count} 人次")
    
    # 2. 会员信息
    if member_info:
        answer_parts.append(f"\n👤 会员信息：")
        answer_parts.append(f"  - 姓名：{member_info.get('name', '')}")
        answer_parts.append(f"  - 等级：{member_info.get('tier', '')}")
        answer_parts.append(f"  - 积分：{member_info.get('points', 0)}")
        answer_parts.append(f"  - 累计消费：¥{member_info.get('total_spent', 0):.2f}")
    
    # 3. 推荐
    if recommendations:
        answer_parts.append(f"\n💡 为您推荐：")
        for rec in recommendations:
            if isinstance(rec, dict):
                answer_parts.append(f"  - {rec.get('product_name', '')}（{rec.get('reason', '')}）")
            else:
                answer_parts.append(f"  - {rec}")
    
    # 4. 优惠券
    if coupons:
        answer_parts.append(f"\n🎁 优惠券：")
        for coupon in coupons:
            if isinstance(coupon, dict):
                answer_parts.append(f"  - {coupon.get('description', '')} 有效期至 {coupon.get('expires_at', '')}")
            else:
                answer_parts.append(f"  - {coupon}")
    
    if not answer_parts:
        answer_parts.append("暂无可分析的数据，请确保摄像头已连接并配置。")
    
    return {
        "final_answer": "\n".join(answer_parts),
        "current_step": "end",
    }


# ----- 构建子图 -----

def build_vision_subgraph():
    """构建视觉分析子图"""
    graph_builder = StateGraph(VisionAgentState)
    
    # 添加节点
    graph_builder.add_node("analyze_video", analyze_video_node)
    graph_builder.add_node("recognize_member", recognize_member_node)
    graph_builder.add_node("member_service", member_service_node)
    graph_builder.add_node("recommend", recommend_node)
    graph_builder.add_node("format", format_response_node)
    
    # 流程
    graph_builder.set_entry_point("analyze_video")
    graph_builder.add_edge("analyze_video", "recognize_member")
    
    # 条件边：如果识别到会员，走会员服务，否则直接格式化
    graph_builder.add_conditional_edges(
        "recognize_member",
        lambda state: "member_service" if state.get("member_id") else "format",
        {
            "member_service": "member_service",
            "format": "format",
        }
    )
    
    graph_builder.add_edge("member_service", "recommend")
    graph_builder.add_edge("recommend", "format")
    graph_builder.add_edge("format", END)
    
    return graph_builder.compile()