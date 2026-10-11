def exam_routes(app, args):
    """Register course, exam, and agent analysis routes."""
    from AIOralExamSystem.routes.course_routes import register_course_routes
    from AIOralExamSystem.routes.exam_routes import register_exam_routes
    from AIOralExamSystem.routes.agent_analysis_routes import register_agent_analysis_routes

    register_exam_routes(app, args)
    register_course_routes(app, args)
    register_agent_analysis_routes(app, args)
    return app
