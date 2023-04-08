bullet_point_characters = ["*", "-", "+", "•", "o", "O", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b",
                           "c", "d"]


def parse_bullet_points(text):
    lines = text.splitlines()
    result = []
    for line in lines:
        if len(line) > 0 and line[0] in bullet_point_characters and line[1] == " ":
            result.append(line[2:])
    return result
