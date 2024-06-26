import os
import shutil

from django.contrib import messages
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.management import call_command
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, TemplateView, FormView

from soil_analysis.domain.repository.landrepository import LandRepository
from soil_analysis.domain.service.landcandidateservice import LandCandidateService
from soil_analysis.domain.service.reports.reportlayout1 import ReportLayout1
from soil_analysis.domain.service.zipfileservice import ZipFileService
from soil_analysis.forms import CompanyCreateForm, LandCreateForm, UploadForm
from soil_analysis.models import Company, Land, LandScoreChemical, LandReview, CompanyCategory, LandLedger, \
    SoilHardnessMeasurementImportErrors, SoilHardnessMeasurement, LandBlock, SamplingOrder, RouteSuggestImport


class Home(TemplateView):
    template_name = "soil_analysis/home.html"


class CompanyListView(ListView):
    model = Company
    template_name = "soil_analysis/company/list.html"

    def get_queryset(self):
        return super().get_queryset().filter(category=CompanyCategory.AGRI_COMPANY)


class CompanyCreateView(CreateView):
    model = Company
    template_name = "soil_analysis/company/create.html"
    form_class = CompanyCreateForm

    def get_success_url(self):
        return reverse('soil:company_detail', kwargs={'pk': self.object.pk})


class CompanyDetailView(DetailView):
    model = Company
    template_name = 'soil_analysis/company/detail.html'


class LandListView(ListView):
    model = Land
    template_name = "soil_analysis/land/list.html"

    def get_queryset(self):
        company = Company(pk=self.kwargs['company_id'])
        return super().get_queryset().filter(company=company)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company(pk=self.kwargs['company_id'])
        land_repository = LandRepository(company)
        land_ledger_map = {land: land_repository.read_landledgers(land) for land in context['object_list']}
        context['company'] = company
        context['land_ledger_map'] = land_ledger_map

        return context


class LandCreateView(CreateView):
    model = Land
    template_name = "soil_analysis/land/create.html"
    form_class = LandCreateForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company(pk=self.kwargs['company_id'])
        context['company'] = company

        return context

    def form_valid(self, form):
        form.instance.company_id = self.kwargs['company_id']

        return super().form_valid(form)

    def get_success_url(self):
        company = Company(pk=self.kwargs['company_id'])
        return reverse('soil:land_detail', kwargs={'company_id': company.id, 'pk': self.object.pk})


class LandDetailView(DetailView):
    model = Land
    template_name = 'soil_analysis/land/detail.html'


class LandReportChemicalListView(ListView):
    model = LandScoreChemical
    template_name = "soil_analysis/landreport/chemical.html"

    def get_queryset(self):
        landledger = LandLedger(self.kwargs['landledger_id'])
        return super().get_queryset().filter(landledger=landledger)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        landledger = LandLedger.objects.get(id=self.kwargs['landledger_id'])

        context['charts'] = ReportLayout1(landledger).publish()
        context['company'] = Company(self.kwargs['company_id'])
        context['landledger'] = landledger
        context['landscores'] = LandScoreChemical.objects.filter(landledger=landledger)
        context['landreview'] = LandReview.objects.filter(landledger=landledger)

        return context


class SoilhardnessUploadView(FormView):
    template_name = 'soil_analysis/soilhardness/form.html'
    form_class = UploadForm
    success_url = reverse_lazy('soil:soilhardness_success')

    def form_valid(self, form):
        # Zipを処理してバッチ実行
        upload_folder = ZipFileService.handle_uploaded_zip(self.request.FILES['file'])
        if os.path.exists(upload_folder):
            call_command('import_soil_hardness', upload_folder)
            shutil.rmtree(upload_folder)

        return super().form_valid(form)


class SoilhardnessSuccessView(TemplateView):
    template_name = 'soil_analysis/soilhardness/success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['import_errors'] = SoilHardnessMeasurementImportErrors.objects.all()
        return context


class SoilhardnessAssociationView(ListView):
    model = SoilHardnessMeasurement
    template_name = 'soil_analysis/soilhardness/association/list.html'

    def get_queryset(self, **kwargs):
        return super().get_queryset() \
            .filter(landblock__isnull=True) \
            .values('setmemory', 'setdatetime') \
            .annotate(cnt=Count('pk')) \
            .order_by('setmemory')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['landledgers'] = LandLedger.objects.all().order_by('pk')
        return context

    @staticmethod
    def post(request, **kwargs):
        """
        R型 で登録するときは、圃場の1ブロックが5点計測なので、採土法（5点法、9点法）の回数を乗ずると、1圃場での採取回数になる
        R型以外のときはIndividualViewへ飛ぶ
        """
        form_landledger = int(request.POST.get('landledger')[0])
        if "btn_individual" in request.POST:
            return HttpResponseRedirect(
                reverse(
                    'soil:soilhardness_association_individual',
                    kwargs={
                        'memory_anchor': int(request.POST.get('btn_individual')),
                        'landledger': form_landledger
                    })
            )

        form_checkboxes = [int(checkbox) for checkbox in request.POST.getlist('form_checkboxes[]')]
        if form_checkboxes:
            landledger = LandLedger.objects.filter(pk=form_landledger).first()
            sampling_times = landledger.sampling_method.times
            total_sampling_times = 5 * sampling_times
            needle = 0
            landblock_orders = SamplingOrder.objects \
                .filter(sampling_method=landledger.sampling_method) \
                .order_by('ordering')
            for memory_anchor in form_checkboxes:
                soilhardness_measurements = SoilHardnessMeasurement.objects \
                    .filter(setmemory__range=(memory_anchor, memory_anchor + (total_sampling_times - 1))) \
                    .order_by('pk')
                for i, soilhardness_measurement in enumerate(soilhardness_measurements):
                    soilhardness_measurement.landblock = landblock_orders[needle].landblock
                    soilhardness_measurement.landledger = landledger
                    forward_the_needle = i > 0 and i % (soilhardness_measurement.setdepth * sampling_times) == 0
                    if forward_the_needle:
                        needle += 1
                SoilHardnessMeasurement.objects.bulk_update(soilhardness_measurements,
                                                            fields=["landblock", "landledger"])

        return HttpResponseRedirect(reverse('soil:soilhardness_association_success'))


class SoilhardnessAssociationIndividualView(ListView):
    model = SoilHardnessMeasurement
    template_name = 'soil_analysis/soilhardness/association/individual/list.html'

    def get_queryset(self, **kwargs):
        form_memory_anchor = self.kwargs.get('memory_anchor')
        form_landledger = self.kwargs.get('landledger')
        landledger = LandLedger.objects.filter(pk=form_landledger).first()
        total_sampling_times = 5 * landledger.sampling_method.times
        return super().get_queryset() \
            .filter(setmemory__range=(form_memory_anchor, form_memory_anchor + (total_sampling_times - 1))) \
            .values('setmemory', 'setdatetime') \
            .annotate(cnt=Count('pk')) \
            .order_by('setmemory')

    def get_context_data(self, **kwargs):
        form_memory_anchor = self.kwargs.get('memory_anchor')
        form_landledger = self.kwargs.get('landledger')
        context = super().get_context_data(**kwargs)
        context['memory_anchor'] = form_memory_anchor
        context['landledger'] = form_landledger
        context['land_blocks'] = LandBlock.objects.order_by('pk').all()
        return context

    def post(self, request, **kwargs):
        """
        R型 以外で登録したいとき
        フォームから25レコードの情報がくるのでそれぞれを更新する
        """
        form_memory_anchor = self.kwargs.get('memory_anchor')
        form_landledger = self.kwargs.get('landledger')
        form_landblocks = request.POST.getlist('landblocks[]')
        landledger = LandLedger.objects.filter(pk=form_landledger).first()
        total_sampling_times = 5 * landledger.sampling_method.times
        soilhardness_measurements = SoilHardnessMeasurement.objects \
            .filter(setmemory__range=(form_memory_anchor, form_memory_anchor + (total_sampling_times - 1))) \
            .order_by('pk')
        for i, soilhardness_measurement in enumerate(soilhardness_measurements):
            needle = i // 60
            soilhardness_measurement.landblock_id = form_landblocks[needle]
            soilhardness_measurement.landledger = landledger
        SoilHardnessMeasurement.objects.bulk_update(soilhardness_measurements,
                                                    fields=["landblock", "landledger"])
        if SoilHardnessMeasurement.objects.filter(landblock__isnull=True).count() == 0:
            return HttpResponseRedirect(reverse('soil:soilhardness_association_success'))

        return HttpResponseRedirect(reverse('soil:soilhardness_association'))


class SoilhardnessAssociationSuccessView(TemplateView):
    template_name = 'soil_analysis/soilhardness/association/success.html'


class RouteSuggestUploadView(FormView):
    template_name = 'soil_analysis/routesuggest/form.html'
    form_class = UploadForm
    success_url = reverse_lazy('soil:routesuggest_ordering')

    def form_valid(self, form):
        """
        Notes: Directions API の地点を制限する
         可能であれば、クエリでのユーザー入力を最大 10 地点に制限します。10 を超える地点を含むリクエストは、課金レートが高くなります。
         https://developers.google.com/maps/optimization-guide?hl=ja#routes
        """
        upload_file: InMemoryUploadedFile = self.request.FILES['file']
        kml_raw = upload_file.read()
        land_candidate_service = LandCandidateService()
        land_candidates = land_candidate_service.parse_kml(kml_raw).list()

        if len(land_candidates) < 2:
            messages.error(self.request, "少なくとも 2 つの場所を指定してください")
            return redirect(self.request.META.get('HTTP_REFERER'))

        if len(land_candidates) > 10:
            messages.error(self.request, "GooglemapAPIのレート上昇制約により 10 地点までしか計算できません")
            return redirect(self.request.META.get('HTTP_REFERER'))

        entities = []
        for land_candidate in land_candidates:
            coordinates_str = land_candidate.center.to_googlemapcoords().get_coords(to_str=True)
            entity = RouteSuggestImport.objects.create(name=land_candidate.name, coords=coordinates_str)
            entities.append(entity)
        RouteSuggestImport.objects.all().delete()
        RouteSuggestImport.objects.bulk_create(entities)

        return super().form_valid(form)


class RouteSuggestOrderingView(ListView):
    model = RouteSuggestImport
    template_name = "soil_analysis/routesuggest/ordering.html"

    def post(self, request, *args, **kwargs):
        order_data = self.request.POST.get('order_data')

        try:
            if order_data:
                order_ids = order_data.split(",")
                for order, order_id in enumerate(order_ids, start=1):
                    route_suggest = RouteSuggestImport.objects.get(pk=order_id)
                    route_suggest.ordering = order
                    route_suggest.save()

            messages.success(request, "Data updated successfully")
            return redirect(reverse_lazy('soil:routesuggest_success'))

        except RouteSuggestImport.DoesNotExist:
            messages.error(request, "Invalid order data provided.")
            return redirect(request.META.get('HTTP_REFERER'))


class RouteSuggestSuccessView(TemplateView):
    template_name = 'soil_analysis/routesuggest/success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        route_suggest_imports = RouteSuggestImport.objects.all().order_by('ordering')
        company_list = []
        land_list = []
        for route_suggest_import in route_suggest_imports:
            company_name, land_name = route_suggest_import.name.split(' - ')
            company_list.append(company_name)
            land_list.append({"name": land_name, "coords": route_suggest_import.coords})

        context['company_list'] = company_list
        context['land_list'] = land_list
        context['coords_list'] = list(land["coords"] for land in land_list)

        return context
