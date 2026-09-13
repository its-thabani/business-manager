from django.urls import path

from apps.web import categories, mapping, reconciliation, simulations, views

app_name = "web"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("insights/", views.insights, name="insights"),
    path("integrations/", views.integrations, name="integrations"),
    path("mapping/", mapping.mapping_list, name="mapping_list"),
    path("mapping/blanks/", mapping.blank_list, name="blank_list"),
    path("mapping/blanks/<int:pk>/", mapping.blank_detail, name="blank_detail"),
    path("mapping/<int:pk>/", mapping.mapping_detail, name="mapping_detail"),
    path("products/", views.products, name="products"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("groups/", views.groups, name="groups"),
    path("groups/<int:pk>/", views.group_detail, name="group_detail"),
    path("customers/", views.customers, name="customers"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("shipping/", views.shipping, name="shipping"),
    path("discounts/", views.discounts, name="discounts"),
    path("refunds/", views.refunds, name="refunds"),
    path("orders/", views.orders, name="orders"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("reconciliation/", reconciliation.reconciliation, name="reconciliation"),
    path("categories/", categories.category_rules, name="category_rules"),
    path("categories/rules/<int:pk>/", categories.category_rule_edit, name="category_rule_edit"),
    path("simulate/", simulations.simulate_view, name="simulate"),
    path("guide/", views.guide, name="guide"),
    path("healthz", views.healthz, name="healthz"),
]
