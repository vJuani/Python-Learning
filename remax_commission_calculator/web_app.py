from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for
)

import sqlite3

from datetime import date

from modules.database import (
    add_agent,
    add_property,
    create_tables,
    delete_agent,
    delete_property,
    get_agent_ranking,
    get_agents,
    get_dashboard_metrics,
    get_operation_record,
    get_operations,
    get_properties,
    search_operations_by_agent,
    search_operations_by_date,
    search_operations_by_id,
    search_operations_by_property,
    update_agent,
    update_property
)

from modules.operations import (
    build_operation_from_selection,
    remove_operation,
    save_calculated_operation,
    update_calculated_operation,
    validate_operation_inputs
)


app = Flask(__name__)
app.secret_key = "dev-secret-key"

AGENT_TYPES = (
    "Alto",
    "Puro",
    "Junior",
    "RAPP"
)

JURISDICTIONS = (
    "CABA",
    "PBA"
)


def get_dashboard_context():
    metrics = get_dashboard_metrics()
    agents = get_agents()
    properties = get_properties()
    ranking = get_agent_ranking(
        limit=3
    )

    return {
        "metrics": metrics,
        "agents": agents,
        "agent_count": len(agents),
        "property_count": len(properties),
        "ranking": ranking
    }


def filter_agents(agents, search_query):
    if search_query == "":
        return agents

    filtered_agents = []

    for agent in agents:
        if search_query.isdigit():
            if agent["id"] == int(search_query):
                filtered_agents.append(agent)
                continue

        if (
            search_query.lower()
            in agent["name"].lower()
        ):
            filtered_agents.append(agent)

    return filtered_agents


def get_agent_by_id(agent_id):
    for agent in get_agents():
        if agent["id"] == agent_id:
            return agent

    return None


def validate_agent_form(name, agent_type):
    errors = []

    if name.strip() == "":
        errors.append(
            "Agent name cannot be empty."
        )

    if agent_type not in AGENT_TYPES:
        errors.append(
            "Invalid agent type."
        )

    return errors


def filter_properties(
    properties,
    search_query,
    operations=None
):
    if search_query == "":
        return properties

    if operations is None:
        operations = get_operations()

    filtered_ids = set()
    query_lower = search_query.lower()

    for property_data in properties:
        property_id = property_data["id"]

        if search_query.isdigit():
            if property_id == int(search_query):
                filtered_ids.add(property_id)
                continue

        if (
            query_lower
            in property_data["address"].lower()
        ):
            filtered_ids.add(property_id)
            continue

        if (
            query_lower
            in property_data["jurisdiction"].lower()
        ):
            filtered_ids.add(property_id)

    for operation in operations:
        property_id = int(
            operation["property_id"]
            .replace("PROP-", "")
        )

        agent_match = (
            query_lower
            in operation["agent"].lower()
        )

        price_match = (
            search_query
            in f"{operation['sale_price']:.2f}"
        ) or (
            search_query
            in str(operation["sale_price"])
        )

        if agent_match or price_match:
            filtered_ids.add(property_id)

    return [
        property_data
        for property_data in properties
        if property_data["id"] in filtered_ids
    ]


def get_property_by_id(property_id):
    for property_data in get_properties():
        if property_data["id"] == property_id:
            return property_data

    return None


def validate_property_form(
    address,
    jurisdiction
):
    errors = []

    if address.strip() == "":
        errors.append(
            "Property address cannot be empty."
        )

    if jurisdiction not in JURISDICTIONS:
        errors.append(
            "Invalid jurisdiction."
        )

    return errors


def filter_operations(search_query):
    if search_query == "":
        return get_operations()

    results = search_operations_by_id(
        search_query
    )

    if len(results) > 0:
        return results

    if "/" in search_query:
        results = search_operations_by_date(
            search_query
        )

        if len(results) > 0:
            return results

    results = search_operations_by_agent(
        search_query
    )

    if len(results) > 0:
        return results

    results = search_operations_by_property(
        search_query
    )

    if len(results) > 0:
        return results

    filtered_operations = []

    for operation in get_operations():
        sale_price_text = (
            f"{operation['sale_price']:.2f}"
        )

        if (
            search_query in sale_price_text
            or search_query
            in str(operation["sale_price"])
        ):
            filtered_operations.append(
                operation
            )

    return filtered_operations


def get_operation_form_values(form):
    return {
        "agent_id": form.get(
            "agent_id",
            ""
        ),
        "property_id": form.get(
            "property_id",
            ""
        ),
        "sale_price": form.get(
            "sale_price",
            ""
        ),
        "commission_rate": form.get(
            "commission_rate",
            ""
        ),
        "was_invoiced": form.get(
            "was_invoiced",
            "no"
        ),
        "vat_amount": form.get(
            "vat_amount",
            "0"
        ),
        "operation_date": form.get(
            "operation_date",
            ""
        )
    }


def render_operation_form(
    form_title,
    submit_label,
    preview_label,
    form_values,
    errors,
    is_edit,
    operation_id=None
):
    return render_template(
        "operations/form.html",
        form_title=form_title,
        submit_label=submit_label,
        preview_label=preview_label,
        form_values=form_values,
        agents=get_agents(),
        properties=get_properties(),
        errors=errors,
        is_edit=is_edit,
        operation_id=operation_id
    )


def process_operation_submission(
    form_values,
    operation_display_id=None
):
    errors, parsed = validate_operation_inputs(
        form_values["agent_id"],
        form_values["property_id"],
        form_values["sale_price"],
        form_values["commission_rate"],
        form_values["was_invoiced"],
        form_values["vat_amount"],
        form_values["operation_date"]
    )

    if len(errors) > 0:
        return errors, None, parsed

    operation = build_operation_from_selection(
        parsed["agent_id"],
        parsed["property_id"],
        parsed["sale_price"],
        parsed["commission_rate"],
        parsed["was_invoiced"],
        parsed["vat_amount"],
        operation_date=parsed["operation_date"],
        operation_display_id=operation_display_id
    )

    return errors, operation, parsed


@app.route("/")
def dashboard():
    context = get_dashboard_context()

    return render_template(
        "dashboard.html",
        **context
    )


@app.route("/agents")
def agents_list():
    search_query = request.args.get(
        "q",
        ""
    ).strip()

    agents = filter_agents(
        get_agents(),
        search_query
    )

    return render_template(
        "agents/list.html",
        agents=agents,
        search_query=search_query,
        agent_count=len(agents)
    )


@app.route(
    "/agents/new",
    methods=[
        "GET",
        "POST"
    ]
)
def agents_new():
    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        agent_type = request.form.get(
            "agent_type",
            ""
        )

        errors = validate_agent_form(
            name,
            agent_type
        )

        if errors:
            return render_template(
                "agents/form.html",
                form_title="New Agent",
                submit_label="Create Agent",
                agent={
                    "name": name,
                    "type": agent_type
                },
                agent_types=AGENT_TYPES,
                errors=errors,
                is_edit=False
            )

        add_agent(
            name,
            agent_type
        )

        flash(
            "Agent added successfully!",
            "success"
        )

        return redirect(
            url_for("agents_list")
        )

    return render_template(
        "agents/form.html",
        form_title="New Agent",
        submit_label="Create Agent",
        agent={
            "name": "",
            "type": ""
        },
        agent_types=AGENT_TYPES,
        errors=[],
        is_edit=False
    )


@app.route(
    "/agents/<int:agent_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
def agents_edit(agent_id):
    agent = get_agent_by_id(
        agent_id
    )

    if agent is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        agent_type = request.form.get(
            "agent_type",
            ""
        )

        errors = validate_agent_form(
            name,
            agent_type
        )

        if errors:
            agent["name"] = name
            agent["type"] = agent_type

            return render_template(
                "agents/form.html",
                form_title="Edit Agent",
                submit_label="Save Changes",
                agent=agent,
                agent_types=AGENT_TYPES,
                errors=errors,
                is_edit=True
            )

        update_agent(
            agent_id,
            name,
            agent_type
        )

        flash(
            "Agent updated successfully!",
            "success"
        )

        return redirect(
            url_for("agents_list")
        )

    return render_template(
        "agents/form.html",
        form_title="Edit Agent",
        submit_label="Save Changes",
        agent=agent,
        agent_types=AGENT_TYPES,
        errors=[],
        is_edit=True
    )


@app.route(
    "/agents/<int:agent_id>/delete",
    methods=["POST"]
)
def agents_delete(agent_id):
    agent = get_agent_by_id(
        agent_id
    )

    if agent is None:
        abort(404)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash(
            "Deletion cancelled. "
            "You must confirm to delete.",
            "error"
        )

        return redirect(
            url_for(
                "agents_edit",
                agent_id=agent_id
            )
        )

    delete_agent(
        agent_id
    )

    flash(
        "Agent deleted successfully!",
        "success"
    )

    return redirect(
        url_for("agents_list")
    )


@app.route("/properties")
def properties_list():
    search_query = request.args.get(
        "q",
        ""
    ).strip()

    properties = filter_properties(
        get_properties(),
        search_query
    )

    return render_template(
        "properties/list.html",
        properties=properties,
        search_query=search_query,
        property_count=len(properties)
    )


@app.route(
    "/properties/new",
    methods=[
        "GET",
        "POST"
    ]
)
def properties_new():
    if request.method == "POST":
        address = request.form.get(
            "address",
            ""
        ).strip()

        jurisdiction = request.form.get(
            "jurisdiction",
            ""
        )

        errors = validate_property_form(
            address,
            jurisdiction
        )

        if errors:
            return render_template(
                "properties/form.html",
                form_title="New Property",
                submit_label="Create Property",
                property_data={
                    "address": address,
                    "jurisdiction": jurisdiction
                },
                jurisdictions=JURISDICTIONS,
                errors=errors,
                is_edit=False
            )

        add_property(
            address,
            jurisdiction
        )

        flash(
            "Property added successfully!",
            "success"
        )

        return redirect(
            url_for("properties_list")
        )

    return render_template(
        "properties/form.html",
        form_title="New Property",
        submit_label="Create Property",
        property_data={
            "address": "",
            "jurisdiction": ""
        },
        jurisdictions=JURISDICTIONS,
        errors=[],
        is_edit=False
    )


@app.route(
    "/properties/<int:property_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
def properties_edit(property_id):
    property_data = get_property_by_id(
        property_id
    )

    if property_data is None:
        abort(404)

    if request.method == "POST":
        address = request.form.get(
            "address",
            ""
        ).strip()

        jurisdiction = request.form.get(
            "jurisdiction",
            ""
        )

        errors = validate_property_form(
            address,
            jurisdiction
        )

        if errors:
            property_data["address"] = address
            property_data["jurisdiction"] = jurisdiction

            return render_template(
                "properties/form.html",
                form_title="Edit Property",
                submit_label="Save Changes",
                property_data=property_data,
                jurisdictions=JURISDICTIONS,
                errors=errors,
                is_edit=True
            )

        update_property(
            property_id,
            address,
            jurisdiction
        )

        flash(
            "Property updated successfully!",
            "success"
        )

        return redirect(
            url_for("properties_list")
        )

    return render_template(
        "properties/form.html",
        form_title="Edit Property",
        submit_label="Save Changes",
        property_data=property_data,
        jurisdictions=JURISDICTIONS,
        errors=[],
        is_edit=True
    )


@app.route(
    "/properties/<int:property_id>/delete",
    methods=["POST"]
)
def properties_delete(property_id):
    property_data = get_property_by_id(
        property_id
    )

    if property_data is None:
        abort(404)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash(
            "Deletion cancelled. "
            "You must confirm to delete.",
            "error"
        )

        return redirect(
            url_for(
                "properties_edit",
                property_id=property_id
            )
        )

    try:
        delete_property(
            property_id
        )

    except sqlite3.IntegrityError:
        flash(
            "Cannot delete this property because "
            "it is linked to existing operations.",
            "error"
        )

        return redirect(
            url_for(
                "properties_edit",
                property_id=property_id
            )
        )

    flash(
        "Property deleted successfully!",
        "success"
    )

    return redirect(
        url_for("properties_list")
    )


@app.route("/operations")
def operations_list():
    search_query = request.args.get(
        "q",
        ""
    ).strip()

    operations = filter_operations(
        search_query
    )

    return render_template(
        "operations/list.html",
        operations=operations,
        search_query=search_query,
        operation_count=len(operations)
    )


@app.route(
    "/operations/<int:operation_id>"
)
def operations_detail(operation_id):
    operation = get_operation_record(
        operation_id
    )

    if operation is None:
        abort(404)

    return render_template(
        "operations/detail.html",
        operation=operation
    )


@app.route(
    "/operations/new",
    methods=[
        "GET",
        "POST"
    ]
)
def operations_new():
    if len(get_agents()) == 0 or len(get_properties()) == 0:
        flash(
            "Add at least one agent and one "
            "property before creating operations.",
            "error"
        )
        return redirect(
            url_for("operations_list")
        )

    if request.method == "POST":
        form_values = get_operation_form_values(
            request.form
        )
        action = request.form.get(
            "action",
            "preview"
        )

        errors, operation, parsed = (
            process_operation_submission(
                form_values
            )
        )

        if len(errors) > 0:
            return render_operation_form(
                "New Operation",
                "Save Operation",
                "Preview Calculation",
                form_values,
                errors,
                is_edit=False
            )

        if action == "preview":
            return render_template(
                "operations/preview.html",
                operation=operation,
                form_values=form_values,
                parsed=parsed,
                is_edit=False,
                operation_id=None,
                back_url=url_for(
                    "operations_new"
                ),
                save_url=url_for(
                    "operations_new"
                )
            )

        save_calculated_operation(
            parsed["agent_id"],
            parsed["property_id"],
            operation
        )

        flash(
            "Operation saved successfully!",
            "success"
        )

        return redirect(
            url_for("operations_list")
        )

    return render_operation_form(
        "New Operation",
        "Save Operation",
        "Preview Calculation",
        {
            "agent_id": "",
            "property_id": "",
            "sale_price": "",
            "commission_rate": "",
            "was_invoiced": "no",
            "vat_amount": "0",
            "operation_date": date.today().strftime(
                "%d/%m/%Y"
            )
        },
        [],
        is_edit=False
    )


@app.route(
    "/operations/<int:operation_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
def operations_edit(operation_id):
    operation = get_operation_record(
        operation_id
    )

    if operation is None:
        abort(404)

    if request.method == "POST":
        form_values = get_operation_form_values(
            request.form
        )
        action = request.form.get(
            "action",
            "preview"
        )

        errors, calculated, parsed = (
            process_operation_submission(
                form_values,
                operation_display_id=operation["id"]
            )
        )

        if len(errors) > 0:
            return render_operation_form(
                "Edit Operation",
                "Save Changes",
                "Preview Calculation",
                form_values,
                errors,
                is_edit=True,
                operation_id=operation_id
            )

        if action == "preview":
            return render_template(
                "operations/preview.html",
                operation=calculated,
                form_values=form_values,
                parsed=parsed,
                is_edit=True,
                operation_id=operation_id,
                back_url=url_for(
                    "operations_edit",
                    operation_id=operation_id
                ),
                save_url=url_for(
                    "operations_edit",
                    operation_id=operation_id
                )
            )

        update_calculated_operation(
            operation_id,
            parsed["agent_id"],
            parsed["property_id"],
            calculated
        )

        flash(
            "Operation updated successfully!",
            "success"
        )

        return redirect(
            url_for("operations_list")
        )

    form_values = {
        "agent_id": str(
            operation["agent_db_id"]
        ),
        "property_id": str(
            operation["property_db_id"]
        ),
        "sale_price": str(
            operation["sale_price"]
        ),
        "commission_rate": str(
            operation["commission_rate"]
        ),
        "was_invoiced": operation[
            "was_invoiced"
        ],
        "vat_amount": str(
            operation["vat_amount"]
        ),
        "operation_date": operation["date"]
    }

    return render_operation_form(
        "Edit Operation",
        "Save Changes",
        "Preview Calculation",
        form_values,
        [],
        is_edit=True,
        operation_id=operation_id
    )


@app.route(
    "/operations/<int:operation_id>/delete",
    methods=["POST"]
)
def operations_delete(operation_id):
    operation = get_operation_record(
        operation_id
    )

    if operation is None:
        abort(404)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash(
            "Deletion cancelled. "
            "You must confirm to delete.",
            "error"
        )

        return redirect(
            url_for(
                "operations_edit",
                operation_id=operation_id
            )
        )

    remove_operation(
        operation_id
    )

    flash(
        "Operation deleted successfully!",
        "success"
    )

    return redirect(
        url_for("operations_list")
    )


if __name__ == "__main__":
    create_tables()

    app.run(
        debug=True
    )
