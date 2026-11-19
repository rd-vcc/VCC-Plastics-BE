"""Aggregates every currently-real, functional module router.

Each included router keeps its exact existing URL prefix (verified against
the original ``routers/*.py`` ``APIRouter(prefix=...)`` declarations). Empty
placeholder module routers (scaffolding with no routes) are intentionally
NOT included here.
"""

from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.administration.users.router import router as users_router
from app.modules.administration.roles.router import router as roles_router
from app.modules.administration.permissions.router import router as permissions_router
from app.modules.administration.system_settings.router import router as system_settings_router
from app.modules.organization.employees.router import router as employees_router
from app.modules.master_data.factory_structure.router import router as factory_structure_router
from app.modules.master_data.products.router import router as products_router
from app.modules.master_data.products.bom_router import router as product_bom_router
from app.modules.master_data.machines.router import router as machines_router
from app.modules.master_data.molds.router import router as molds_router
from app.modules.master_data.materials.router import router as materials_router
from app.modules.master_data.production_tools.router import router as production_tools_router
from app.modules.master_data.measuring_equipment.router import router as measuring_equipment_router
from app.modules.master_data.quality_standards.router import router as quality_standards_router
from app.modules.master_data.process_parameter_standards.router import router as process_parameter_standards_router
from app.modules.master_data.shift_calendar.router import router as shift_calendar_router
from app.modules.master_data.reason_codes.router import router as reason_codes_router
from app.modules.production.planning.router import router as production_planning_router
from app.modules.production.orders.router import router as production_orders_router
from app.modules.master_data.customers.router import router as customers_router
from app.modules.material.inventory.router import router as material_inventory_router
from app.modules.material.requirements.router import router as material_requirements_router
from app.modules.material.allocations.router import router as material_allocations_router
from app.modules.material.usage.router import router as material_usage_router
from app.modules.production.work_orders.router import router as work_orders_router
from app.modules.production.execution.router import router as production_execution_router
from app.modules.production.results.router import router as production_results_router
from app.modules.production.dashboard.router import router as production_dashboard_router
from app.modules.files.router import router as files_router
from app.modules.machine.data_router import router as machine_data_router
from app.modules.machine.downtime_router import router as downtime_router
from app.modules.machine.alarm_router import router as alarm_router
from app.modules.machine.monitoring_router import router as machine_monitoring_router
from app.modules.mold.router import router as mold_management_router
from app.modules.maintenance.router import router as maintenance_router
from app.modules.quality.router import router as quality_router
from app.modules.traceability.router import router as traceability_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.reports.router import router as reports_router
from app.modules.administration.audit.router import router as audit_router
from app.modules.notifications.router import router as notifications_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(roles_router)
api_router.include_router(permissions_router)
api_router.include_router(system_settings_router)
api_router.include_router(employees_router)
api_router.include_router(factory_structure_router)
api_router.include_router(products_router)
api_router.include_router(product_bom_router)
api_router.include_router(machines_router)
api_router.include_router(molds_router)
api_router.include_router(materials_router)
api_router.include_router(production_tools_router)
api_router.include_router(measuring_equipment_router)
api_router.include_router(quality_standards_router)
api_router.include_router(process_parameter_standards_router)
api_router.include_router(shift_calendar_router)
api_router.include_router(reason_codes_router)
api_router.include_router(production_planning_router)
api_router.include_router(production_orders_router)
api_router.include_router(customers_router)
api_router.include_router(material_inventory_router)
api_router.include_router(material_requirements_router)
api_router.include_router(material_allocations_router)
api_router.include_router(material_usage_router)
api_router.include_router(work_orders_router)
api_router.include_router(production_execution_router)
api_router.include_router(production_results_router)
api_router.include_router(production_dashboard_router)
api_router.include_router(files_router)
api_router.include_router(machine_data_router)
api_router.include_router(downtime_router)
api_router.include_router(alarm_router)
api_router.include_router(machine_monitoring_router)
api_router.include_router(mold_management_router)
api_router.include_router(maintenance_router)
api_router.include_router(quality_router)
api_router.include_router(traceability_router)
api_router.include_router(dashboard_router)
api_router.include_router(reports_router)
api_router.include_router(audit_router)
api_router.include_router(notifications_router)
