from django import forms

from .models import Item, Invoice


class ItemCreateForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ('warehouse', 'serial_number', 'name', 'price', 'pos_x', 'pos_y', 'pos_z', 'staff')


class InvoiceCreateForm(forms.ModelForm):
    # TODO: 会社名を選んだら、会社名の請求担当者しか選べない形にしたい
    #  https://blog.narito.ninja/detail/50

    class Meta:
        model = Invoice
        fields = ('company', 'billing_person', 'rental_start_date', 'rental_end_date', 'staff')
        exclude = ['billing_status']
        widgets = {
            'company': forms.Select(attrs={'class': 'form-control'}),
            'billing_person': forms.Select(attrs={'class': 'form-control'}),
            'rental_start_date': forms.DateInput(attrs={'class': 'form-control'}),
            'rental_end_date': forms.DateInput(attrs={'class': 'form-control'}),
            'staff': forms.Select(attrs={'class': 'form-control'}),
        }

    def clean_company(self):
        company = self.cleaned_data['company']
        if 'クサリク' in company.name:
            raise forms.ValidationError('「クサリク」を含む取引先は選択できなくなりました（取引停止）')
        return company
