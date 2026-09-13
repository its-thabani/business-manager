import apps.core.models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="shopify_unit_cost",
            field=apps.core.models.MoneyField(
                blank=True,
                decimal_places=2,
                help_text="Shopify Cost per item, if one was entered on the variant. Used only when no supplier cost exists.",
                max_digits=12,
                null=True,
            ),
        ),
    ]
