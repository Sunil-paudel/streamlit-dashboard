def update_quiz_grade_debug(quiz_cmid, user_id, grade, course_id):
    st.write("DEBUG: Attempting quiz grade update")
    st.write(f"- Course ID: {course_id}")
    st.write(f"- Quiz CMID: {quiz_cmid}")
    st.write(f"- User ID: {user_id}")
    st.write(f"- Grade: {grade}")
    
    params = {
        'source': 'mod/quiz',
        'courseid': course_id,
        'component': 'mod_quiz',
        'activityid': quiz_cmid,
        'itemnumber': 0,
        'grades[0][studentid]': user_id,
        'grades[0][grade]': grade
    }
    
    result = moodle_call("core_grades_update_grades", params)
    st.write("DEBUG: Moodle API result:", result)
    return result
