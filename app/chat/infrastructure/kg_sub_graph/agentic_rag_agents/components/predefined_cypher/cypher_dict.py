from typing import Dict

predefined_cypher_dict: Dict[str, str] = {
    # 产品类查询
    "product_by_name": "MATCH (p:Product) WHERE p.ProductName CONTAINS $product_name RETURN p.ProductName, p.UnitPrice, p.UnitsInStock, p.CategoryName",
    "product_by_category": "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE c.CategoryName = $category_name RETURN p.ProductName, p.UnitPrice, p.UnitsInStock",
    "product_by_supplier": "MATCH (p:Product)-[:SUPPLIED_BY]->(s:Supplier) WHERE s.CompanyName = $supplier_name RETURN p.ProductName, p.UnitPrice, p.UnitsInStock",
    "products_low_stock": "MATCH (p:Product) WHERE toInteger(p.UnitsInStock) < 10 RETURN p.ProductName, p.UnitsInStock, p.CategoryName ORDER BY toInteger(p.UnitsInStock)",
    "products_popular": "MATCH (p:Product)<-[:ABOUT]-(r:Review) RETURN p.ProductName, count(r) as ReviewCount, avg(toFloat(r.Rating)) as AvgRating ORDER BY ReviewCount DESC LIMIT 10",
    
    # 客户类查询
    "customer_by_name": "MATCH (c:Customer) WHERE c.CompanyName CONTAINS $customer_name RETURN c.CompanyName, c.ContactName, c.Phone, c.Country",
    "customer_orders": "MATCH (c:Customer)-[:PLACED]->(o:Order) WHERE c.CompanyName = $customer_name RETURN o.orderId, o.OrderDate, o.ShippedDate",
    "customer_purchase_history": "MATCH (c:Customer)-[:PLACED]->(o:Order)-[:CONTAINS]->(p:Product) WHERE c.CompanyName = $customer_name RETURN p.ProductName, o.OrderDate, p.UnitPrice",
    
    # 订单类查询
    "order_by_id": "MATCH (o:Order) WHERE o.orderId = $order_id RETURN o.OrderDate, o.RequiredDate, o.ShippedDate, o.CustomerName",
    "order_details": "MATCH (o:Order)-[contains:CONTAINS]->(p:Product) WHERE o.orderId = $order_id RETURN p.ProductName, contains.Quantity, contains.UnitPrice, toFloat(contains.Quantity) * toFloat(contains.UnitPrice) as TotalPrice",
    "recent_orders": "MATCH (o:Order) RETURN o.orderId, o.OrderDate, o.CustomerName ORDER BY o.OrderDate DESC LIMIT 10",
    "delayed_orders": "MATCH (o:Order) WHERE o.RequiredDate < o.ShippedDate OR (o.RequiredDate < date() AND o.ShippedDate IS NULL) RETURN o.orderId, o.OrderDate, o.RequiredDate, o.ShippedDate, o.CustomerName",
    
    # 供应商类查询
    "supplier_by_country": "MATCH (s:Supplier) WHERE s.Country = $country RETURN s.CompanyName, s.ContactName, s.Phone",
    "supplier_products": "MATCH (s:Supplier)<-[:SUPPLIED_BY]-(p:Product) WHERE s.CompanyName = $supplier_name RETURN p.ProductName, p.UnitPrice, p.UnitsInStock",
    
    # 类别类查询
    "all_categories": "MATCH (c:Category) RETURN c.CategoryName, c.Description",
    "category_products": "MATCH (c:Category)<-[:BELONGS_TO]-(p:Product) WHERE c.CategoryName = $category_name RETURN p.ProductName, p.UnitPrice, p.UnitsInStock",
    "category_product_count": "MATCH (c:Category)<-[:BELONGS_TO]-(p:Product) RETURN c.CategoryName, count(p) as ProductCount ORDER BY ProductCount DESC",
    
    # 员工类查询
    "employee_by_name": "MATCH (e:Employee) WHERE e.FirstName + ' ' + e.LastName CONTAINS $employee_name RETURN e.FirstName, e.LastName, e.Title, e.HireDate",
    "employee_processed_orders": "MATCH (e:Employee)-[:PROCESSED]->(o:Order) WHERE e.FirstName + ' ' + e.LastName = $employee_name RETURN o.orderId, o.OrderDate, o.CustomerName",
    
    # 评论类查询
    "product_reviews": "MATCH (p:Product)<-[:ABOUT]-(r:Review) WHERE p.ProductName = $product_name RETURN r.CustomerName, r.Rating, r.ReviewText, r.ReviewDate ORDER BY r.ReviewDate DESC",
    "top_rated_products": "MATCH (p:Product)<-[:ABOUT]-(r:Review) WITH p.ProductName as ProductName, avg(toFloat(r.Rating)) as AvgRating, count(r) as ReviewCount WHERE ReviewCount > 3 RETURN ProductName, AvgRating, ReviewCount ORDER BY AvgRating DESC LIMIT 10",
    
    # 销售分析类查询
    "product_sales": "MATCH (o:Order)-[c:CONTAINS]->(p:Product) WHERE p.ProductName = $product_name RETURN sum(toFloat(c.Quantity) * toFloat(c.UnitPrice)) as TotalSales",
    "category_sales": "MATCH (o:Order)-[c:CONTAINS]->(p:Product)-[:BELONGS_TO]->(cat:Category) RETURN cat.CategoryName, sum(toFloat(c.Quantity) * toFloat(c.UnitPrice)) as TotalSales ORDER BY TotalSales DESC",
    "monthly_sales": "MATCH (o:Order)-[c:CONTAINS]->(p:Product) RETURN substring(o.OrderDate, 0, 7) as Month, sum(toFloat(c.Quantity) * toFloat(c.UnitPrice)) as Sales ORDER BY Month",
    
    # 智能家居相关查询（示例）
    "smart_home_products": "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE c.CategoryName CONTAINS '智能' RETURN p.ProductName, p.UnitPrice, p.UnitsInStock, c.CategoryName",
    "smart_speakers": "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE c.CategoryName = '智能音箱' RETURN p.ProductName, p.UnitPrice, p.UnitsInStock",
    "smart_lighting": "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE c.CategoryName = '智能照明' RETURN p.ProductName, p.UnitPrice, p.UnitsInStock"
}

QUERY_DESCRIPTIONS = {
    "product_by_name": "查询特定名称的产品信息，包括价格、库存和类别。适用于用户询问某个具体产品的详细信息。",
    "product_by_category": "查询特定类别下的所有产品信息。适用于用户想了解某个产品类别下有哪些商品。",
    "product_by_supplier": "查询特定供应商提供的所有产品。适用于用户想了解某个供应商提供了哪些商品。",
    "products_low_stock": "查询库存不足（低于10个）的产品信息。适用于用户询问哪些产品需要补货或库存紧张。",
    "products_popular": "查询最受欢迎的产品（基于评论数量）。适用于用户询问哪些产品最受欢迎或销量最好。",
    "customer_by_name": "查询特定客户的详细信息。适用于用户询问某个客户的联系方式或地址。",
    "customer_orders": "查询特定客户的所有订单信息。适用于用户询问某个客户的订单历史。",
    "customer_purchase_history": "查询特定客户的购买历史，包括购买的产品和日期。适用于用户询问客户购买了哪些产品。",
    "order_by_id": "查询特定订单ID的基本信息。适用于用户询问某个订单的状态、日期等。",
    "order_details": "查询特定订单的详细信息，包括包含的产品、数量和价格。适用于用户询问订单中包含哪些商品。",
    "recent_orders": "查询最近的10个订单。适用于用户询问最近有哪些新订单。",
    "delayed_orders": "查询延迟发货的订单。适用于用户询问哪些订单发货延迟或未按时发货。",
    "supplier_by_country": "查询特定国家的所有供应商。适用于用户询问某个国家有哪些供应商。",
    "supplier_products": "查询特定供应商提供的所有产品。适用于用户询问某个供应商提供了哪些产品。",
    "all_categories": "查询所有产品类别及其描述。适用于用户询问有哪些产品类别。",
    "category_products": "查询特定类别下的所有产品。适用于用户询问某个类别下有哪些产品。",
    "category_product_count": "查询每个类别包含的产品数量。适用于用户询问各类别的产品数量分布。",
    "employee_by_name": "查询特定员工的基本信息。适用于用户询问某个员工的职位、入职日期等。",
    "employee_processed_orders": "查询特定员工处理的所有订单。适用于用户询问某个员工处理了哪些订单。",
    "product_reviews": "查询特定产品的所有评论。适用于用户询问某个产品的用户评价。",
    "top_rated_products": "查询评分最高的产品。适用于用户询问哪些产品评分最高或最受好评。",
    "product_sales": "查询特定产品的总销售额。适用于用户询问某个产品的销售情况或销售额。",
    "category_sales": "查询各产品类别的总销售额。适用于用户询问哪些类别销售额最高或各类别的销售情况。",
    "monthly_sales": "查询每月的销售情况。适用于用户询问销售额的月度变化或趋势。",
    "smart_home_products": "查询所有智能家居相关产品。适用于用户询问有哪些智能家居产品。",
    "smart_speakers": "查询所有智能音箱类产品。适用于用户询问有哪些智能音箱或语音助手产品。",
    "smart_lighting": "查询所有智能照明类产品。适用于用户询问有哪些智能灯或智能照明产品。",
}
