from modules.agents import (
    add_agent,
    delete_agent,
    edit_agent,
    list_agents
)

from modules.dashboard import show_dashboard

from modules.database import create_tables

from modules.exports import (
    export_agent_report,
    export_date_report,
    export_history_csv,
    export_history_excel,
    export_property_report
)

from modules.menus import (
    agents_menu,
    menu,
    properties_menu,
    reports_menu,
    search_menu
)

from modules.operations import new_calculation

from modules.properties import (
    add_property,
    delete_property,
    edit_property,
    list_properties
)

from modules.reports import show_history

from modules.search import (
    search_by_agent,
    search_by_date,
    search_by_id,
    search_by_property
)


def manage_agents():
    while True:
        option = agents_menu()

        if option == 1:
            list_agents()

        elif option == 2:
            add_agent()

        elif option == 3:
            edit_agent()

        elif option == 4:
            delete_agent()

        elif option == 5:
            break


def manage_properties():
    while True:
        option = properties_menu()

        if option == 1:
            list_properties()

        elif option == 2:
            add_property()

        elif option == 3:
            edit_property()

        elif option == 4:
            delete_property()

        elif option == 5:
            break


def manage_search():
    while True:
        option = search_menu()

        if option == 1:
            search_by_agent()

        elif option == 2:
            search_by_id()

        elif option == 3:
            search_by_property()

        elif option == 4:
            search_by_date()

        elif option == 5:
            break


def manage_reports():
    while True:
        option = reports_menu()

        if option == 1:
            export_history_csv()

        elif option == 2:
            export_history_excel()

        elif option == 3:
            export_agent_report()

        elif option == 4:
            export_property_report()

        elif option == 5:
            export_date_report()

        elif option == 6:
            break


def main():
    create_tables()

    while True:
        option = menu()

        if option == 1:
            new_calculation()

        elif option == 2:
            show_history()

        elif option == 3:
            manage_agents()

        elif option == 4:
            manage_properties()

        elif option == 5:
            show_dashboard()

        elif option == 6:
            manage_search()

        elif option == 7:
            manage_reports()

        elif option == 8:
            break

    print("Goodbye!")


if __name__ == "__main__":
    main()