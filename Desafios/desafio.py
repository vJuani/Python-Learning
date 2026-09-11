name = "Juan"
age = 25
location = "Martinez"

def menu():
    print("1 - Show Name")
    print("2 - Show Age")
    print("3 - Show Location")
    print("4 - Exit")

    option = int(input("Enter your option: "))
    return option
def show_name(name):
    print("Your name is:", name)
def show_age(age):
    print("Your age is:", age)
def show_location(location):
    print("Your location is:", location)
def main():
    option = menu()

    while option != 4:

        if option == 1:
            show_name(name)

        elif option == 2:
            show_age(age)

        elif option == 3:
            show_location(location)

        else:
            print("Invalid option")

        option = menu()

if __name__ == "__main__":
    main()