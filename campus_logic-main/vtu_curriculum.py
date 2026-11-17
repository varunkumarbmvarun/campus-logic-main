"""Verified Semester V subject records from VTU's official 2022 scheme PDFs."""


COMMON_SUBJECTS = (
    ("BRMK557", "Research Methodology and IPR", "Core", "", True),
    ("BNSK559", "National Service Scheme (NSS)", "Elective", "Mandatory non-credit option", False),
    ("BPEK559", "Physical Education (PE) (Sports and Athletics)", "Elective", "Mandatory non-credit option", False),
    ("BYOK559", "Yoga", "Elective", "Mandatory non-credit option", False),
)

CURRICULA = {
    "civil": {
        "name": "Civil Engineering",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/58civsch.pdf",
        "syllabus_url": "https://vtu.ac.in/pdf/2022_3to8/3civsyll.pdf",
        "subjects": (
            ("BCV501", "Construction Management and Entrepreneurship", "Core", "", True),
            ("BCV502", "Geotechnical Engineering", "Core", "", True),
            ("BCV503", "Concrete Technology", "Core", "", True),
            ("BCV504", "Environmental Engineering Lab", "Lab", "", True),
            ("BCV586", "Mini Project/Extensive Survey Project", "Project", "", True),
            ("BESK508", "Environmental Studies", "Core", "", True),
            ("BCV515A", "Numerical Methods in Civil Engineering", "Elective", "Professional Elective I", False),
            ("BC515B", "Occupational Safety and Health Monitoring", "Elective", "Professional Elective I", False),
            ("BCV515C", "Solid Waste Management", "Elective", "Professional Elective I", False),
            ("BCV515D", "Remote Sensing and GIS", "Elective", "Professional Elective I", False),
        ),
    },
    "cse": {
        "name": "Computer Science & Engineering",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/38csesch.pdf",
        "syllabus_url": "https://vtu.ac.in/pdf/2022_3to8/3csesyll.pdf",
        "subjects": (
            ("BCS501", "Software Engineering & Project Management", "Core", "", True),
            ("BCS502", "Computer Networks", "Core", "", True),
            ("BCS503", "Theory of Computation", "Core", "", True),
            ("BCSL504", "Web Technology Lab", "Lab", "", True),
            ("BCS586", "Mini Project", "Project", "", True),
            ("BCS508", "Environmental Studies and E-waste Management", "Core", "", True),
            ("BAI515A", "Computer Graphics", "Elective", "Professional Elective I", False),
            ("BCS515B", "Artificial Intelligence", "Elective", "Professional Elective I", False),
            ("BCS515C", "Unix System Programming", "Elective", "Professional Elective I", False),
            ("BCS515D", "Distributed Systems", "Elective", "Professional Elective I", False),
        ),
    },
    "eee": {
        "name": "Electrical & Electronics Engineering",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/58eesch.pdf",
        "syllabus_url": "https://vtu.ac.in/pdf/2022_3to8/3eesyll.pdf",
        "subjects": (
            ("BEE501", "Engineering Management and Entrepreneurship", "Core", "", True),
            ("BEE502", "Signals & DSP", "Core", "", True),
            ("BEE503", "Power Electronics", "Core", "", True),
            ("BEEL504", "Power Electronics Lab", "Lab", "", True),
            ("BEE586", "Mini Project", "Project", "", True),
            ("BESK508", "Environmental Studies", "Core", "", True),
            ("BEE515A", "High Voltage Engineering", "Elective", "Professional Elective I", False),
            ("BEE515B", "Power Electronics for Renewable Energy Systems", "Elective", "Professional Elective I", False),
            ("BEE515C", "Electric Vehivle Fundamentals", "Elective", "Professional Elective I", False),
            ("BEE515D", "Fundamentals of VLSI Design", "Elective", "Professional Elective I", False),
        ),
    },
    "industrial-engineering-management": {
        "name": "Industrial Engineering & Management",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/iemsch.pdf",
        "syllabus_url": "",
        "subjects": (
            ("BIM501", "Introduction to Management & Entrepreneurship", "Core", "", True),
            ("BIM502", "Statistical Process control", "Core", "", True),
            ("BIM503", "Operation Management", "Core", "", True),
            ("BIML504", "Quality Engineering Lab", "Lab", "", True),
            ("BIM586", "Mini Project", "Project", "", True),
            ("BESK508", "Environmental Studies", "Core", "", True),
            ("BIM515A", "Digital Manufacturing", "Elective", "Professional Elective I", False),
            ("BIM515B", "Management Information System", "Elective", "Professional Elective I", False),
            ("BIM515C", "Industrial Robotics", "Elective", "Professional Elective I", False),
            ("BIM515D", "Financial Management", "Elective", "Professional Elective I", False),
        ),
    },
    "robotics-automation": {
        "name": "Automation and Robotics (VTU syllabus: Robotics & Automation)",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/58robosch.pdf",
        "syllabus_url": "https://vtu.ac.in/pdf/2022_3to8/3robosyll.pdf",
        "equivalence_url": "https://vtu.ac.in/wp-content/uploads/2024/05/Circular740-Syllabus-of-Robotics-Automation-and-Automation-Robotics-2021-2022-Scheme.pdf",
        "subjects": (
            ("BRA501", "Managerial Economics for Robotics", "Core", "", True),
            ("BRA502", "Hydraulics & Pneumatics", "Core", "", True),
            ("BRA503", "Robot Operating Systems", "Core", "", True),
            ("BRAL504", "Virtual Instrumentation Lab", "Lab", "", True),
            ("BRA586", "Mini Project", "Project", "", True),
            ("BESK508", "Environmental Studies", "Core", "", True),
            ("BRA515A", "Design of Automation System", "Elective", "Professional Elective I", False),
            ("BRA515B", "Autonomous Robots", "Elective", "Professional Elective I", False),
            ("BRA515C", "Wireless Sensors Networks for Robotics", "Elective", "Professional Elective I", False),
            ("BRA515D", "Image Processing", "Elective", "Professional Elective I", False),
        ),
    },
    "silk-technology": {
        "name": "Silk Technology",
        "scheme_url": "https://vtu.ac.in/pdf/2022_3to8/silksch.pdf",
        "syllabus_url": "https://vtu.ac.in/pdf/2022_3to8/silksyll.pdf",
        "subjects": (
            ("BTX501", "Management and Entrepreneurship in Textile Industry", "Core", "", True),
            ("BTX502", "Fashion Design and Garment Manufacture", "Core", "", True),
            ("BST503", "Silk Reeling Technology", "Core", "", True),
            ("BSTL504", "Silk Reeling Technology lab", "Lab", "", True),
            ("BTX586", "Mini Project", "Project", "", True),
            ("BESK508", "Environmental Studies", "Core", "", True),
            ("BTX515A", "Advanced Yarn and Fabric Manufacturing", "Elective", "Professional Elective I", False),
            ("BTX515B", "High Performance and Speciality Fibres", "Elective", "Professional Elective I", False),
            ("BTX515C", "Human Resource Management in Textile Industry", "Elective", "Professional Elective I", False),
            ("BTX515D", "Financial Management", "Elective", "Professional Elective I", False),
        ),
    },
}


def semester_five_subjects(branch_key):
    curriculum = CURRICULA[branch_key]
    return [
        {
            "code": code,
            "name": name,
            "category": category,
            "elective_group": elective_group,
            "is_required": is_required,
        }
        for code, name, category, elective_group, is_required in (
            COMMON_SUBJECTS + curriculum["subjects"]
        )
    ]
