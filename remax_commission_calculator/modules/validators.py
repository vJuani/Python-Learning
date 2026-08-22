def get_float(message):
    while True:
        try:
            value = float(input(message))

            if value < 0:
                print("The value cannot be negative.")
            else:
                return value

        except ValueError:
            print(
                "Invalid input. "
                "Please enter a valid number."
            )


def parse_positive_float(value):
    try:
        number = float(value)

        if number < 0:
            return None, (
                "The value cannot be negative."
            )

        return number, None

    except (TypeError, ValueError):
        return None, (
            "Invalid input. "
            "Please enter a valid number."
        )