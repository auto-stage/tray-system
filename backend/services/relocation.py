def build_relocation_plan(
    current_slots,
    used_trays,
    target_order
):
    """
    사용한 Tray들끼리만 재배치 계획을 만든다.

    current_slots 예:
    [
        "TRAY 05", "TRAY 06",
        "TRAY 03", "TRAY 04",
        "TRAY 01", "TRAY 02"
    ]

    used_trays 예:
    [
        "TRAY 01",
        "TRAY 02"
    ]

    target_order 예:
    [
        "TRAY 02",
        "TRAY 01"
    ]
    """

    # -----------------------------------------
    # 사용 Tray가 현재 차지하고 있는 위치 찾기
    # -----------------------------------------

    used_positions = []

    for index, tray in enumerate(current_slots):

        if tray in used_trays:
            used_positions.append(index)


    # -----------------------------------------
    # 안전 검사
    # -----------------------------------------

    if len(used_positions) != len(used_trays):

        raise ValueError(
            "현재 선반에서 사용 Tray 위치를 찾을 수 없습니다."
        )


    if set(target_order) != set(used_trays):

        raise ValueError(
            "재배치 결과에는 사용한 Tray만 포함되어야 합니다."
        )


    # -----------------------------------------
    # 최종 선반 배열 생성
    #
    # 사용하지 않은 Tray 위치는 절대 변경하지 않음
    # -----------------------------------------

    final_slots = current_slots.copy()


    for position, tray in zip(
        used_positions,
        target_order
    ):

        final_slots[position] = tray


    # -----------------------------------------
    # 이동 계획 생성
    # -----------------------------------------

    moves = []


    for tray in used_trays:

        old_position = current_slots.index(tray)

        new_position = final_slots.index(tray)


        if old_position != new_position:

            moves.append(
                {
                    "tray": tray,

                    "from_slot":
                        old_position,

                    "to_slot":
                        new_position
                }
            )


    return {
        "used_trays":
            used_trays,

        "moves":
            moves,

        "final_slots":
            final_slots
    }