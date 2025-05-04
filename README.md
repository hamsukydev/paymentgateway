# HamsukyPay Payment Gateway

A Django-based payment gateway similar to PayStack for processing online payments securely in Nigeria and across Africa.

![HamsukyPay Logo](static/img/logo.png)

## Aims and Objectives

### Vision
To create a robust, secure, and developer-friendly payment gateway solution that empowers Nigerian and African businesses to receive payments globally while providing a seamless experience for both merchants and customers.

### Primary Aims
1. **Financial Inclusion**: Bridge the gap in digital financial services across Nigeria and Africa, enabling more businesses to participate in the global digital economy.
2. **Security and Compliance**: Establish a payment infrastructure that adheres to international security standards and regulatory requirements while protecting sensitive financial data.
3. **Local Context**: Provide payment solutions tailored to the specific needs of the Nigerian market, including support for local payment methods and currencies.
4. **Developer Experience**: Create a platform that developers can easily integrate with minimal friction, regardless of their technical expertise level.

### Objectives
1. **Comprehensive Payment Solutions**:
   - Process card payments securely from all major providers
   - Support local payment methods including bank transfers, USSD, and mobile money
   - Enable recurring payments and subscription management
   - Facilitate international payments with multi-currency support

2. **Merchant Empowerment**:
   - Provide robust analytics and reporting tools for business insights
   - Offer customizable checkout experiences adaptable to any business model
   - Implement flexible pricing suitable for businesses of all sizes
   - Deliver excellent documentation and developer tools

3. **Technical Excellence**:
   - Maintain 99.9% system uptime and reliability
   - Process transactions with minimal latency (<2 seconds)
   - Scale infrastructure to handle growing transaction volumes
   - Implement robust security measures including encryption, tokenization, and fraud detection

4. **Customer-Centric Approach**:
   - Create intuitive user interfaces for both merchants and end-users
   - Provide responsive customer support and technical assistance
   - Continuously improve based on user feedback and market demands
   - Ensure accessibility and compatibility across devices

Through achieving these aims and objectives, HamsukyPay strives to become a leading payment solution provider that contributes positively to the growth of digital commerce in Nigeria and across Africa.

## Features

### Core Payment Features
- Process card payments from major providers
- Bank transfers and direct debit support
- USSD and mobile money integration
- Multiple currency support with automatic conversion
- Payment links and invoicing
- Recurring billing and subscription management

### Merchant Features
- Intuitive merchant dashboard with real-time analytics
- Customer management and segmentation
- Detailed transaction history and reporting
- Customizable checkout experiences
- Payment plan creation and management
- Support ticket system

### Developer Tools
- Comprehensive REST API for integration
- SDKs for popular programming languages
- Webhook notifications for real-time updates
- Sandbox environment for testing
- Extensive API documentation with code examples

### Advanced Security
- PCI-DSS compliance
- Strong customer authentication (3DS)
- Fraud detection and prevention system
- Tokenization for secure recurring payments
- AML (Anti-Money Laundering) checks

### Administrative Interface
- Modern, customized admin dashboard
- Comprehensive transaction management
- User and merchant account administration
- Support ticket management system
- System monitoring and analytics

## Screenshots

### Merchant Dashboard
![Merchant Dashboard](static/img/screenshots/merchant-dashboard.png)

### Admin Dashboard
![Admin Dashboard](static/img/screenshots/admin-dashboard.png)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/hamsukypay.git
cd hamsukypay
```

2. Create a virtual environment and activate it:
```bash
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the root directory with the following variables:
```
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
HAMSUKYPAY_SECRET_KEY=sk_test_yoursecretkey
HAMSUKYPAY_PUBLIC_KEY=pk_test_yourpublickey
DATABASE_URL=sqlite:///db.sqlite3
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@example.com
EMAIL_HOST_PASSWORD=youremailpassword
EMAIL_USE_TLS=True
```

5. Run migrations:
```bash
python manage.py makemigrations
python manage.py migrate
```

6. Create a superuser:
```bash
python manage.py createsuperuser
```

7. Run the development server:
```bash
python manage.py runserver
```

8. Access the application:
   - Main website: http://127.0.0.1:8000/
   - Admin interface: http://127.0.0.1:8000/admin/
   - Custom admin dashboard: http://127.0.0.1:8000/admin-custom/dashboard/
   - Merchant dashboard: http://127.0.0.1:8000/merchant/dashboard/

## Usage

### REST API Endpoints

#### Payment Endpoints

- `POST /api/payment/initialize/`: Initialize a payment
  ```json
  {
    "email": "customer@example.com",
    "amount": 10000.00,
    "currency": "NGN",
    "description": "Payment for Product XYZ",
    "metadata": {"custom_field": "custom_value"}
  }
  ```

- `GET /api/payment/verify/?reference=REFERENCE`: Verify a payment

- `POST /api/payment/charge/`: Charge a card or bank account
  ```json
  {
    "email": "customer@example.com",
    "amount": 10000.00,
    "card": {
      "number": "4111111111111111",
      "expiry_month": "09",
      "expiry_year": "25",
      "cvv": "123"
    }
  }
  ```

#### Subscription Endpoints

- `POST /api/subscription/create/`: Create a subscription
  ```json
  {
    "email": "customer@example.com",
    "plan_id": 1
  }
  ```

- `GET /api/subscription/list/`: List all subscriptions for a customer
  ```json
  {
    "email": "customer@example.com"
  }
  ```

- `POST /api/subscription/cancel/`: Cancel a subscription
  ```json
  {
    "subscription_id": "SUB_123456"
  }
  ```

#### Customer Endpoints

- `POST /api/customer/create/`: Create a new customer
  ```json
  {
    "email": "customer@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "phone": "+2341234567890"
  }
  ```

- `GET /api/customer/get/?email=customer@example.com`: Get customer details

### Web Interface

- Home page: `/`
- Payment checkout: `/payment/checkout/<reference>/`
- Payment success: `/payment/success/<reference>/`
- Merchant dashboard: `/merchant/dashboard/`
- Merchant analytics: `/merchant/analytics/`
- Merchant customers: `/merchant/customers/`
- Merchant transactions: `/merchant/transactions/`
- Merchant settings: `/merchant/settings/`

### Admin Interface

Access the standard admin interface at `/admin/` to manage:
- Customers
- Transactions
- Payment Plans
- Subscriptions
- Support Tickets

Access the custom admin dashboard at `/admin-custom/dashboard/` for:
- Analytics overview with charts and metrics
- Transaction management
- Merchant verification
- Customer management
- Support ticket system

## Development

### Extending the Payment Gateway

To extend the gateway for actual payment processing:

1. Modify the `PaymentProcessor` class in `payments/payment_processor.py` to integrate with an actual payment service provider.
2. Update the payment checkout template to use the payment provider's frontend SDK.
3. Implement proper error handling and security measures for production use.

### Customizing the Admin Dashboard

The admin dashboard uses Bootstrap 5 and Chart.js for visualization. To customize:

1. Edit the templates in `templates/admin_custom/` directory.
2. Modify the dashboard views in `payments/views.py` under the admin view functions.
3. Update the JavaScript for charts in the template files.

### Adding New Payment Methods

To add support for new payment methods:

1. Create a new processor class that extends the base `PaymentProcessor`.
2. Implement the required methods for the specific payment method.
3. Update the checkout experience to include the new payment option.
4. Add appropriate validation and error handling.

## Security Considerations

For production use, ensure you:
- Use HTTPS for all communications
- Properly validate and sanitize all input data
- Implement proper authentication and authorization
- Keep your API keys secure
- Follow PCI DSS guidelines if handling card data directly
- Implement proper error logging and monitoring
- Regularly update dependencies to address security vulnerabilities
- Set up proper rate limiting to prevent brute force attacks
- Use strong encryption for sensitive data
- Implement multi-factor authentication for admin access

## Testing

Run the test suite:

```bash
python manage.py test payments
```

For specific test cases:

```bash
python manage.py test payments.tests.test_payment_processor
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgements

- [Django](https://www.djangoproject.com/)
- [Django REST Framework](https://www.django-rest-framework.org/)
- [Bootstrap](https://getbootstrap.com/)
- [Chart.js](https://www.chartjs.org/)
- [Font Awesome](https://fontawesome.com/)